import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Lead, OutreachChannel, OutreachDirection, OutreachLog, OutreachStatus
from schemas import OutreachLogResponse

router = APIRouter(prefix="/leads", tags=["drafting"])

_MODEL = "claude-sonnet-4-6"


class DraftRequest(BaseModel):
    channel: OutreachChannel
    tone: str = "empathetic"          # professional | empathetic | direct
    extra_context: Optional[str] = None


def _get_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "ANTHROPIC_API_KEY is not set.",
                "instructions": "Add ANTHROPIC_API_KEY=<your key> to your .env file and restart.",
            },
        )
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "anthropic package not installed.",
                "instructions": "Run: pip install anthropic",
            },
        )


_SYSTEM_PROMPT = (
    "You are an assistant for a real estate wholesaler. "
    "Draft personalized, compliant outreach messages to property owners. "
    "Be respectful, concise, and transparent about the intent to purchase. "
    "SMS rules: keep the core message under 160 characters when possible; "
    "always append 'Reply STOP to opt out.' on its own line. "
    "Email rules: the very first line must be 'Subject: <compelling subject>', "
    "then a blank line, then the body — professional but warm. "
    "Never make false claims, never impersonate a government agency, "
    "never fabricate hardship details not given to you."
)


def _user_prompt(
    lead: Lead,
    channel: OutreachChannel,
    tone: str,
    extra_context: Optional[str],
) -> str:
    tone_map = {
        "empathetic": "warm and empathetic — acknowledge any difficulty the owner may be facing",
        "professional": "professional and businesslike",
        "direct": "direct and to the point",
    }
    tone_desc = tone_map.get(tone, tone_map["empathetic"])

    lines = [f"Property address: {lead.address}"]
    if lead.city:
        lines.append(f"City: {lead.city}")
    if lead.county:
        lines.append(f"County: {lead.county} County")
    if lead.owner_names:
        lines.append(f"Owner(s): {', '.join(lead.owner_names)}")
    if lead.filing_type and lead.filing_type.value != "none":
        lines.append(f"Public filing type: {lead.filing_type.value.replace('_', ' ')}")
    if lead.filing_date:
        lines.append(f"Filing date: {lead.filing_date.strftime('%B %d, %Y')}")
    if lead.estimated_equity and lead.estimated_equity >= 20_000:
        lines.append(f"Estimated equity: ${lead.estimated_equity:,.0f}")
    if (lead.mailing_address
            and lead.mailing_address.lower().strip() != lead.address.lower().strip()):
        lines.append("Note: owner appears to be absentee (mailing address differs from property)")

    prompt = (
        f"Draft a first-contact {channel.value} outreach message.\n"
        f"Tone: {tone_desc}.\n\n"
        "Lead details:\n" + "\n".join(f"- {l}" for l in lines)
    )
    if extra_context:
        prompt += f"\n\nAdditional context from the user:\n{extra_context}"
    return prompt


@router.post("/{lead_id}/draft-message", response_model=OutreachLogResponse, status_code=201)
def draft_message(lead_id: int, payload: DraftRequest, db: Session = Depends(get_db)):
    """
    Call Claude to draft a first-contact outreach message.
    The result is saved as a pending_approval outreach log entry — nothing is sent automatically.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.do_not_contact:
        raise HTTPException(
            status_code=403,
            detail="Lead is marked Do Not Contact — AI drafting blocked.",
        )

    client = _get_client()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_prompt(lead, payload.channel, payload.tone, payload.extra_context)}],
    )
    content = response.content[0].text.strip()

    log = OutreachLog(
        lead_id=lead_id,
        channel=payload.channel,
        direction=OutreachDirection.outbound,
        content=content,
        status=OutreachStatus.pending_approval,
        ai_drafted=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(log)
    # Update lead's last_contact_date so scoring idle-decay resets
    lead.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(log)
    return log
