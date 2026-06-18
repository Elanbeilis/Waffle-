import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float,
    ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import relationship

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LeadSource(str, enum.Enum):
    propstream = "propstream"
    propertyshark = "propertyshark"
    public_record = "public_record"
    manual = "manual"


class FilingType(str, enum.Enum):
    lis_pendens = "lis_pendens"
    notice_of_default = "notice_of_default"
    notice_of_sale = "notice_of_sale"
    standard_listing = "standard_listing"
    fsbo = "fsbo"
    none = "none"


class PipelineStage(str, enum.Enum):
    new = "new"
    skip_traced = "skip_traced"
    contacted = "contacted"
    responded = "responded"
    negotiating = "negotiating"
    under_contract = "under_contract"
    assigned = "assigned"
    closed = "closed"
    dead = "dead"


class SkipTraceStatus(str, enum.Enum):
    not_needed = "not_needed"
    pending = "pending"
    complete = "complete"
    failed = "failed"


class OutreachChannel(str, enum.Enum):
    sms = "sms"
    email = "email"
    call = "call"


class OutreachDirection(str, enum.Enum):
    outbound = "outbound"
    inbound = "inbound"


class OutreachStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    sent = "sent"
    failed = "failed"
    discarded = "discarded"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)

    # Property info
    address = Column(String, nullable=False)
    city = Column(String)
    county = Column(String)
    owner_names = Column(JSON, default=list)       # ["John Doe", "Jane Doe"]
    mailing_address = Column(String)               # absentee if != address

    # Source / filing
    lead_source = Column(Enum(LeadSource), default=LeadSource.manual, nullable=False)
    filing_type = Column(Enum(FilingType), default=FilingType.none, nullable=False)
    filing_date = Column(DateTime)

    # Financials
    estimated_equity = Column(Float)

    # Contact info (populated after skip trace)
    phones = Column(JSON, default=list)            # ["+15165550100", ...]
    emails = Column(JSON, default=list)            # ["owner@example.com", ...]

    # Workflow
    skip_trace_status = Column(Enum(SkipTraceStatus), default=SkipTraceStatus.not_needed, nullable=False)
    do_not_contact = Column(Boolean, default=False, nullable=False)
    do_not_contact_reason = Column(String)         # e.g. "opt-out reply: STOP"
    pipeline_stage = Column(Enum(PipelineStage), default=PipelineStage.new, nullable=False)

    # Dates
    last_contact_date = Column(DateTime)
    next_followup_date = Column(DateTime)

    # Scoring & notes
    score = Column(Float, default=0.0)
    notes = Column(Text)

    # Dedup helper — normalized "{address}|{owner}" stored on insert
    dedup_key = Column(String, index=True)

    # Timestamps
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    outreach_logs = relationship(
        "OutreachLog", back_populates="lead", cascade="all, delete-orphan", order_by="OutreachLog.created_at"
    )


class OutreachLog(Base):
    __tablename__ = "outreach_log"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)

    channel = Column(Enum(OutreachChannel), nullable=False)
    direction = Column(Enum(OutreachDirection), nullable=False)
    content = Column(Text)
    ai_drafted = Column(Boolean, default=False, nullable=False)
    sent_at = Column(DateTime)
    status = Column(Enum(OutreachStatus), default=OutreachStatus.pending_approval, nullable=False)

    # For opt-out tracking
    opt_out_detected = Column(Boolean, default=False)

    created_at = Column(DateTime, default=_utcnow, nullable=False)

    lead = relationship("Lead", back_populates="outreach_logs")
