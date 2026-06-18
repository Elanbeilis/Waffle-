import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Lead, OutreachLog, OutreachStatus
from schemas import OutreachLogCreate, OutreachLogResponse, OutreachLogUpdate

router = APIRouter(tags=["outreach"])

OPT_OUT_PATTERN = re.compile(
    r"\b(stop|unsubscribe|remove|opt.?out|cancel|end|quit|no\s+more|take\s+me\s+off)\b",
    re.IGNORECASE,
)


def _detect_opt_out(content: str | None) -> bool:
    if not content:
        return False
    return bool(OPT_OUT_PATTERN.search(content))


# ---------------------------------------------------------------------------
# Routes scoped under a lead
# ---------------------------------------------------------------------------

@router.get("/leads/{lead_id}/outreach", response_model=list[OutreachLogResponse])
def list_outreach(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead.outreach_logs


@router.post("/leads/{lead_id}/outreach", response_model=OutreachLogResponse, status_code=201)
def create_outreach_log(lead_id: int, payload: OutreachLogCreate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    opt_out = _detect_opt_out(payload.content)
    log = OutreachLog(
        lead_id=lead_id,
        opt_out_detected=opt_out,
        **payload.model_dump(),
    )

    if opt_out and not lead.do_not_contact:
        lead.do_not_contact = True
        lead.do_not_contact_reason = f"Opt-out detected in inbound {payload.channel} reply"
        lead.updated_at = datetime.now(timezone.utc)

    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Routes for individual log entries
# ---------------------------------------------------------------------------

@router.get("/outreach/{log_id}", response_model=OutreachLogResponse)
def get_outreach_log(log_id: int, db: Session = Depends(get_db)):
    log = db.get(OutreachLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")
    return log


@router.patch("/outreach/{log_id}", response_model=OutreachLogResponse)
def update_outreach_log(log_id: int, payload: OutreachLogUpdate, db: Session = Depends(get_db)):
    log = db.get(OutreachLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")

    update_data = payload.model_dump(exclude_unset=True)

    # Re-run opt-out check if content changed
    if "content" in update_data:
        update_data["opt_out_detected"] = _detect_opt_out(update_data["content"])

    for field, value in update_data.items():
        setattr(log, field, value)

    # If opt-out now detected, propagate to lead
    if log.opt_out_detected:
        lead = db.get(Lead, log.lead_id)
        if lead and not lead.do_not_contact:
            lead.do_not_contact = True
            lead.do_not_contact_reason = f"Opt-out detected in outreach log #{log_id}"
            lead.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(log)
    return log


@router.delete("/outreach/{log_id}", status_code=204)
def delete_outreach_log(log_id: int, db: Session = Depends(get_db)):
    log = db.get(OutreachLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")
    db.delete(log)
    db.commit()


# ---------------------------------------------------------------------------
# Pending approvals queue (used by dashboard)
# ---------------------------------------------------------------------------

@router.get("/outreach/pending", response_model=list[OutreachLogResponse])
def list_pending_approvals(db: Session = Depends(get_db)):
    return (
        db.query(OutreachLog)
        .filter(OutreachLog.status == OutreachStatus.pending_approval)
        .order_by(OutreachLog.created_at.desc())
        .all()
    )
