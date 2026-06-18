from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from models import (
    FilingType, LeadSource, OutreachChannel, OutreachDirection,
    OutreachStatus, PipelineStage, SkipTraceStatus,
)


# ---------------------------------------------------------------------------
# Lead schemas
# ---------------------------------------------------------------------------

class LeadBase(BaseModel):
    address: str
    city: Optional[str] = None
    county: Optional[str] = None
    owner_names: Optional[list[str]] = None
    mailing_address: Optional[str] = None
    lead_source: LeadSource = LeadSource.manual
    filing_type: FilingType = FilingType.none
    filing_date: Optional[datetime] = None
    estimated_equity: Optional[float] = None
    phones: Optional[list[str]] = None
    emails: Optional[list[str]] = None
    skip_trace_status: SkipTraceStatus = SkipTraceStatus.not_needed
    do_not_contact: bool = False
    do_not_contact_reason: Optional[str] = None
    pipeline_stage: PipelineStage = PipelineStage.new
    last_contact_date: Optional[datetime] = None
    next_followup_date: Optional[datetime] = None
    notes: Optional[str] = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    owner_names: Optional[list[str]] = None
    mailing_address: Optional[str] = None
    lead_source: Optional[LeadSource] = None
    filing_type: Optional[FilingType] = None
    filing_date: Optional[datetime] = None
    estimated_equity: Optional[float] = None
    phones: Optional[list[str]] = None
    emails: Optional[list[str]] = None
    skip_trace_status: Optional[SkipTraceStatus] = None
    do_not_contact: Optional[bool] = None
    do_not_contact_reason: Optional[str] = None
    pipeline_stage: Optional[PipelineStage] = None
    last_contact_date: Optional[datetime] = None
    next_followup_date: Optional[datetime] = None
    notes: Optional[str] = None


class LeadResponse(LeadBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    score: float
    dedup_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    existing_lead_id: Optional[int] = None
    message: str


# ---------------------------------------------------------------------------
# Outreach log schemas
# ---------------------------------------------------------------------------

class OutreachLogCreate(BaseModel):
    channel: OutreachChannel
    direction: OutreachDirection
    content: Optional[str] = None
    ai_drafted: bool = False
    sent_at: Optional[datetime] = None
    status: OutreachStatus = OutreachStatus.pending_approval


class OutreachLogUpdate(BaseModel):
    content: Optional[str] = None
    status: Optional[OutreachStatus] = None
    sent_at: Optional[datetime] = None
    opt_out_detected: Optional[bool] = None


class OutreachLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    channel: OutreachChannel
    direction: OutreachDirection
    content: Optional[str]
    ai_drafted: bool
    sent_at: Optional[datetime]
    status: OutreachStatus
    opt_out_detected: bool
    created_at: datetime
