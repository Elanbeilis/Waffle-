import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Lead, PipelineStage
from schemas import DuplicateCheckResult, LeadCreate, LeadResponse, LeadUpdate
from scoring import calculate_score

router = APIRouter(prefix="/leads", tags=["leads"])


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

_STREET_ABBR = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "road": "rd", "lane": "ln", "court": "ct", "place": "pl", "way": "wy",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def _normalize_address(addr: str) -> str:
    addr = addr.lower().strip()
    addr = re.sub(r"[.,#]", " ", addr)
    tokens = addr.split()
    tokens = [_STREET_ABBR.get(t, t) for t in tokens]
    return " ".join(tokens)


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().strip().split())


def _make_dedup_key(address: str, owner_names: list[str]) -> str:
    norm_addr = _normalize_address(address)
    norm_owners = sorted(_normalize_name(n) for n in (owner_names or []))
    return f"{norm_addr}|{'&'.join(norm_owners)}"


def _find_duplicate(db: Session, address: str, owner_names: list[str]) -> Optional[Lead]:
    key = _make_dedup_key(address, owner_names)
    return db.query(Lead).filter(Lead.dedup_key == key).first()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[LeadResponse])
def list_leads(
    stage: Optional[PipelineStage] = Query(None),
    county: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    needs_followup: Optional[bool] = Query(None),
    sort_by: str = Query("score", pattern="^(score|last_contact_date|created_at|next_followup_date)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(Lead)

    if stage:
        q = q.filter(Lead.pipeline_stage == stage)
    if county:
        q = q.filter(Lead.county.ilike(f"%{county}%"))
    if source:
        q = q.filter(Lead.lead_source == source)
    if needs_followup:
        now = datetime.now(timezone.utc)
        q = q.filter(Lead.next_followup_date <= now)

    col = getattr(Lead, sort_by)
    q = q.order_by(col.desc() if sort_dir == "desc" else col.asc())

    return q.offset(skip).limit(limit).all()


@router.post("", response_model=LeadResponse, status_code=201)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    dup = _find_duplicate(db, payload.address, payload.owner_names or [])
    if dup:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Duplicate lead detected — same address + owner already exists.",
                "existing_lead_id": dup.id,
                "existing_address": dup.address,
                "existing_owner_names": dup.owner_names,
            },
        )

    lead = Lead(**payload.model_dump())
    lead.dedup_key = _make_dedup_key(payload.address, payload.owner_names or [])
    lead.score = calculate_score(lead)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadResponse)
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    update_data = payload.model_dump(exclude_unset=True)

    # If address or owner_names changed, recheck dedup key (don't block — just update key)
    addr_changed = "address" in update_data or "owner_names" in update_data
    for field, value in update_data.items():
        setattr(lead, field, value)

    if addr_changed:
        lead.dedup_key = _make_dedup_key(lead.address, lead.owner_names or [])

    lead.updated_at = datetime.now(timezone.utc)
    lead.score = calculate_score(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete(lead)
    db.commit()


@router.post("/{lead_id}/recalculate-score", response_model=LeadResponse)
def recalculate_score(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.score = calculate_score(lead)
    lead.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/check-duplicate", response_model=DuplicateCheckResult)
def check_duplicate(address: str, owner_names: list[str], db: Session = Depends(get_db)):
    dup = _find_duplicate(db, address, owner_names)
    if dup:
        return DuplicateCheckResult(
            is_duplicate=True,
            existing_lead_id=dup.id,
            message=f"Duplicate: lead #{dup.id} at '{dup.address}' already exists.",
        )
    return DuplicateCheckResult(is_duplicate=False, message="No duplicate found.")
