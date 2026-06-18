from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import get_db
from models import Lead, PipelineStage, SkipTraceStatus
from routers.leads import _normalize_phone
from schemas import LeadResponse
from scoring import calculate_score
from stubs.skip_trace import SkipTraceResult, skip_trace_lead

router = APIRouter(prefix="/skip-trace", tags=["skip-trace"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_result(lead: Lead, result: SkipTraceResult) -> None:
    """Apply a SkipTraceResult onto a lead (mutates in place, no db.commit)."""
    if result.error:
        lead.skip_trace_status = SkipTraceStatus.failed
        note = f"[Skip trace failed {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {result.error}]"
    else:
        existing_phones = set(lead.phones or [])
        existing_emails = set(lead.emails or [])
        new_phones = [_normalize_phone(p) for p in result.phones if p.strip()]
        new_emails = [e.lower().strip() for e in result.emails if e.strip()]
        lead.phones = list(existing_phones | set(new_phones)) or lead.phones
        lead.emails = list(existing_emails | set(new_emails)) or lead.emails
        lead.skip_trace_status = SkipTraceStatus.complete
        if lead.pipeline_stage == PipelineStage.new:
            lead.pipeline_stage = PipelineStage.skip_traced
        note = (
            f"[Skip trace {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
            f"({result.match_quality.value} match): "
            f"{len(result.phones)} phone(s), {len(result.emails)} email(s)]"
        )

    lead.notes = f"{lead.notes}\n{note}".strip() if lead.notes else note
    lead.updated_at = datetime.now(timezone.utc)
    lead.score = calculate_score(lead)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@router.get("/queue", response_model=list[LeadResponse])
def get_skip_trace_queue(db: Session = Depends(get_db)):
    """
    All leads that are candidates for skip tracing:
    - Explicitly queued (skip_trace_status = pending)  OR
    - New leads not yet traced (stage = new AND status = not_needed)
    DNC leads excluded.
    """
    return (
        db.query(Lead)
        .filter(
            Lead.do_not_contact == False,
            or_(
                Lead.skip_trace_status == SkipTraceStatus.pending,
                and_(
                    Lead.pipeline_stage == PipelineStage.new,
                    Lead.skip_trace_status == SkipTraceStatus.not_needed,
                ),
            ),
        )
        .order_by(Lead.score.desc())
        .all()
    )


@router.get("/stats")
def skip_trace_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    counts = dict(
        db.query(Lead.skip_trace_status, func.count(Lead.id))
        .group_by(Lead.skip_trace_status)
        .all()
    )
    queued = (
        db.query(func.count(Lead.id))
        .filter(Lead.skip_trace_status == SkipTraceStatus.pending)
        .scalar() or 0
    )
    needs = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.do_not_contact == False,
            Lead.pipeline_stage == PipelineStage.new,
            Lead.skip_trace_status == SkipTraceStatus.not_needed,
        )
        .scalar() or 0
    )
    return {
        "by_status": {s.value: counts.get(s, 0) for s in SkipTraceStatus},
        "queued": queued,
        "needs_trace": needs,
        "total_actionable": queued + needs,
    }


# ---------------------------------------------------------------------------
# Queue / de-queue a single lead
# ---------------------------------------------------------------------------

@router.patch("/{lead_id}/status")
def set_skip_trace_status(
    lead_id: int,
    status: SkipTraceStatus,
    db: Session = Depends(get_db),
):
    """Set skip_trace_status directly — e.g. mark 'pending' to queue a lead."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.skip_trace_status = status
    lead.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"lead_id": lead_id, "skip_trace_status": status.value}


# ---------------------------------------------------------------------------
# Single-lead provider trigger
# ---------------------------------------------------------------------------

@router.post("/{lead_id}/trigger", response_model=LeadResponse)
def trigger_skip_trace(lead_id: int, db: Session = Depends(get_db)):
    """
    Run the configured skip trace provider for one lead.
    Returns 501 with instructions if the stub hasn't been implemented yet.
    """
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.do_not_contact:
        raise HTTPException(status_code=422, detail="Lead is marked do_not_contact — skip trace blocked.")

    try:
        result = skip_trace_lead(lead_id)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "message": "Skip trace provider not implemented.",
                "instructions": "Open stubs/skip_trace.py and implement skip_trace_lead().",
                "stub_error": str(exc),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Provider error: {exc}")

    _apply_result(lead, result)
    db.commit()
    db.refresh(lead)
    return lead


# ---------------------------------------------------------------------------
# Batch trigger
# ---------------------------------------------------------------------------

class BatchSkipTraceRequest(BaseModel):
    lead_ids: Optional[list[int]] = None
    all_queued: bool = False


@router.post("/batch")
def batch_skip_trace(body: BatchSkipTraceRequest, db: Session = Depends(get_db)):
    """
    Run skip trace for multiple leads.

    Pass `lead_ids` for an explicit list, or `all_queued: true` to process
    every lead currently in the skip trace queue.
    """
    if body.all_queued:
        queue_leads = get_skip_trace_queue(db=db)
        lead_ids = [l.id for l in queue_leads]
    elif body.lead_ids:
        lead_ids = body.lead_ids
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide lead_ids or set all_queued: true",
        )

    summary = {
        "total": len(lead_ids),
        "succeeded": 0,
        "failed": 0,
        "skipped_dnc": 0,
        "not_implemented": False,
        "per_lead": [],
    }

    for lid in lead_ids:
        lead = db.get(Lead, lid)
        if not lead:
            summary["per_lead"].append({"lead_id": lid, "status": "not_found"})
            summary["failed"] += 1
            continue
        if lead.do_not_contact:
            summary["per_lead"].append({"lead_id": lid, "status": "skipped_dnc"})
            summary["skipped_dnc"] += 1
            continue

        try:
            result = skip_trace_lead(lid)
            _apply_result(lead, result)
            db.commit()
            entry_status = "failed" if result.error else "ok"
            summary["succeeded" if entry_status == "ok" else "failed"] += 1
            summary["per_lead"].append({
                "lead_id": lid,
                "status": entry_status,
                "match_quality": result.match_quality.value,
                "phones_found": len(result.phones),
                "emails_found": len(result.emails),
                "error": result.error,
            })
        except NotImplementedError:
            db.rollback()
            summary["not_implemented"] = True
            # No point continuing — provider isn't wired in
            remaining = len(lead_ids) - summary["succeeded"] - summary["failed"] - summary["skipped_dnc"]
            summary["failed"] += remaining
            summary["per_lead"].append({"lead_id": lid, "status": "not_implemented"})
            break
        except Exception as exc:
            db.rollback()
            summary["failed"] += 1
            summary["per_lead"].append({"lead_id": lid, "status": "error", "error": str(exc)})

    return summary
