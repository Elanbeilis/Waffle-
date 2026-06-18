"""
Outreach sending — Twilio (SMS) and SendGrid (email).

Compliance gate order (all checked before any send attempt):
  1. DNC     — hard block if lead.do_not_contact
  2. Status  — log must be pending_approval or approved
  3. Quiet   — SMS blocked between SMS_QUIET_HOUR_START and SMS_QUIET_HOUR_END (local tz)
  4. Contact — phone required for SMS, email required for email

On success: log.status → sent, lead.last_contact_date updated,
            stage auto-advances skip_traced → contacted.
"""
import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import OutreachChannel, OutreachLog, OutreachStatus, PipelineStage
from schemas import OutreachLogResponse
from scoring import calculate_score

router = APIRouter(prefix="/outreach", tags=["sending"])


class SendRequest(BaseModel):
    content: Optional[str] = None  # if set, replaces log content before sending


# ---------------------------------------------------------------------------
# Compliance gate
# ---------------------------------------------------------------------------

def _quiet_hours_check() -> None:
    tz_name = os.getenv("LOCAL_TIMEZONE", "America/New_York")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/New_York")
        tz_name = "America/New_York"
    now_local = datetime.now(tz)
    hour = now_local.hour
    q_start = int(os.getenv("SMS_QUIET_HOUR_START", "21"))
    q_end = int(os.getenv("SMS_QUIET_HOUR_END", "8"))
    if hour >= q_start or hour < q_end:
        raise HTTPException(
            status_code=403,
            detail={
                "blocked": True,
                "reason": "quiet_hours",
                "message": (
                    f"SMS quiet hours are active ({q_start:02d}:00–{q_end:02d}:00 {tz_name}). "
                    f"Current time: {now_local.strftime('%H:%M %Z')}. "
                    f"Try again after {q_end:02d}:00."
                ),
            },
        )


def _compliance_gate(log: OutreachLog) -> None:
    lead = log.lead

    # 1 — DNC
    if lead.do_not_contact:
        reason = f": {lead.do_not_contact_reason}" if lead.do_not_contact_reason else "."
        raise HTTPException(
            status_code=403,
            detail={
                "blocked": True,
                "reason": "do_not_contact",
                "message": f"Lead #{lead.id} is marked Do Not Contact{reason}",
            },
        )

    # 2 — Status
    if log.status not in (OutreachStatus.pending_approval, OutreachStatus.approved):
        raise HTTPException(
            status_code=409,
            detail={
                "blocked": True,
                "reason": "invalid_status",
                "message": (
                    f"Log status is '{log.status.value}' — only pending_approval "
                    "or approved logs can be sent."
                ),
            },
        )

    # 3 + 4 — Channel-specific
    if log.channel == OutreachChannel.sms:
        _quiet_hours_check()
        if not lead.phones:
            raise HTTPException(
                status_code=422,
                detail={
                    "blocked": True,
                    "reason": "no_phone",
                    "message": "No phone number on file. Add one via skip trace or the edit form.",
                },
            )
    elif log.channel == OutreachChannel.email:
        if not lead.emails:
            raise HTTPException(
                status_code=422,
                detail={
                    "blocked": True,
                    "reason": "no_email",
                    "message": "No email address on file. Add one via skip trace or the edit form.",
                },
            )


# ---------------------------------------------------------------------------
# Carrier helpers
# ---------------------------------------------------------------------------

def _send_sms(to: str, body: str) -> str:
    """Send via Twilio; returns message SID."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_ = os.getenv("TWILIO_FROM_NUMBER", "")
    if not all([sid, token, from_]):
        raise HTTPException(
            status_code=503,
            detail={
                "blocked": True,
                "reason": "twilio_not_configured",
                "message": "Twilio credentials are not set.",
                "instructions": (
                    "Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER "
                    "to your .env file and restart."
                ),
            },
        )
    try:
        from twilio.rest import Client
        return Client(sid, token).messages.create(body=body, from_=from_, to=to).sid
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail={
                "blocked": True,
                "reason": "twilio_not_installed",
                "message": "twilio package not installed.",
                "instructions": "Run: pip install twilio",
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"blocked": True, "reason": "twilio_error", "message": str(exc)},
        )


def _send_email(to: str, content: str) -> str:
    """Send via SendGrid; returns HTTP status code as string."""
    api_key = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("EMAIL_FROM", "noreply@example.com")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "blocked": True,
                "reason": "sendgrid_not_configured",
                "message": "SENDGRID_API_KEY is not set.",
                "instructions": "Add SENDGRID_API_KEY and EMAIL_FROM to your .env file and restart.",
            },
        )

    # Claude formats email as "Subject: ...\n\n<body>" — parse it out
    subject = "Message from Real Estate Investor"
    body = content
    lines = content.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][8:].strip()
        # Skip the subject line and any following blank line
        rest = lines[1:]
        while rest and not rest[0].strip():
            rest = rest[1:]
        body = "\n".join(rest)

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import Mail
        mail = Mail(
            from_email=from_email,
            to_emails=to,
            subject=subject,
            plain_text_content=body,
        )
        response = sg_module.SendGridAPIClient(api_key=api_key).send(mail)
        return str(response.status_code)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail={
                "blocked": True,
                "reason": "sendgrid_not_installed",
                "message": "sendgrid package not installed.",
                "instructions": "Run: pip install sendgrid",
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"blocked": True, "reason": "sendgrid_error", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# Send endpoint
# ---------------------------------------------------------------------------

@router.post("/{log_id}/send", response_model=OutreachLogResponse)
def send_outreach(log_id: int, payload: SendRequest, db: Session = Depends(get_db)):
    """
    Send an outreach log entry via Twilio (SMS) or SendGrid (email).

    Runs the full compliance gate before touching any external API.
    Passing `content` in the body replaces the stored draft first,
    so the user's final edits in the approval queue are preserved.

    On success the log moves to `sent` and the lead's last_contact_date
    is stamped; `skip_traced` leads auto-advance to `contacted`.
    """
    log = db.get(OutreachLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log not found")

    # Accept final content edit from the approval UI
    if payload.content is not None:
        log.content = payload.content

    _compliance_gate(log)

    lead = log.lead
    now = datetime.now(timezone.utc)

    if log.channel == OutreachChannel.sms:
        _send_sms(lead.phones[0], log.content or "")
    elif log.channel == OutreachChannel.email:
        _send_email(lead.emails[0], log.content or "")
    elif log.channel == OutreachChannel.call:
        raise HTTPException(
            status_code=501,
            detail={
                "message": "Automated calling is not supported.",
                "instructions": "Log call outreach manually on the lead detail page.",
            },
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported channel: {log.channel.value}")

    # Mark sent
    log.status = OutreachStatus.sent
    log.sent_at = now

    # Update lead
    if lead.pipeline_stage == PipelineStage.skip_traced:
        lead.pipeline_stage = PipelineStage.contacted
    lead.last_contact_date = now
    lead.updated_at = now
    lead.score = calculate_score(lead)

    db.commit()
    db.refresh(log)
    return log
