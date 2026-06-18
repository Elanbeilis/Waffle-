"""
Skip trace stub — wire in your provider here.

Interface contract:
    skip_trace_lead(lead_id: int) -> SkipTraceResult

The router (routers/skip_trace.py) calls this function, applies the result to the
lead (phones, emails, status, stage), and returns the updated lead.

To implement:
  1. Install your provider's SDK or use httpx/requests.
  2. Set SKIP_TRACE_API_KEY in your .env file.
  3. Replace the `raise NotImplementedError` in skip_trace_lead() below.

Provider references:
  BatchSkipTracing  https://batchskiptracing.com/api-docs
  TLOxp             https://tloxp.com/api
  IDI Data          https://www.ididata.com/
  Spokeo API        https://developer.spokeo.com/
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # avoid circular imports


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class MatchQuality(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    no_match = "no_match"


@dataclass
class SkipTraceResult:
    lead_id: int
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    match_quality: MatchQuality = MatchQuality.no_match
    raw_response: dict = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Lead context helper — use inside your implementation to get lead data
# ---------------------------------------------------------------------------

@dataclass
class LeadInfo:
    lead_id: int
    address: str
    city: str | None
    county: str | None
    owner_names: list[str]
    mailing_address: str | None


def get_lead_info(lead_id: int) -> LeadInfo:
    """Fetch the data your provider needs to build a lookup request."""
    from database import SessionLocal
    from models import Lead

    db = SessionLocal()
    try:
        lead = db.get(Lead, lead_id)
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")
        return LeadInfo(
            lead_id=lead.id,
            address=lead.address,
            city=lead.city,
            county=lead.county,
            owner_names=lead.owner_names or [],
            mailing_address=lead.mailing_address,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Provider stub — implement this
# ---------------------------------------------------------------------------

def skip_trace_lead(lead_id: int) -> SkipTraceResult:
    """
    Look up phone numbers and email addresses for a lead.

    Steps to implement:
      1. Call get_lead_info(lead_id) to get the property address and owner name.
      2. Build the provider API request (usually first name, last name, address).
      3. POST to the provider endpoint using os.environ['SKIP_TRACE_API_KEY'].
      4. Parse phones and emails out of the response.
      5. Assess match quality (high = name+address confirmed, medium = address only, etc.).
      6. Return SkipTraceResult.

    Example skeleton for BatchSkipTracing:

        import httpx
        info = get_lead_info(lead_id)
        first, *rest = (info.owner_names[0] if info.owner_names else "Unknown").split()
        last = rest[-1] if rest else ""

        resp = httpx.post(
            "https://batchskiptracing.com/api/v2/person/search",
            headers={"Authorization": f"Bearer {os.environ['SKIP_TRACE_API_KEY']}"},
            json={
                "firstName": first, "lastName": last,
                "address": info.address, "city": info.city or "",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        phones = [r["phoneNumber"] for r in data.get("phoneResults", [])]
        emails = [r["email"] for r in data.get("emailResults", [])]
        quality = MatchQuality.high if data.get("confidence", 0) >= 80 else MatchQuality.medium

        return SkipTraceResult(
            lead_id=lead_id,
            phones=phones,
            emails=emails,
            match_quality=quality,
            raw_response=data,
        )
    """
    raise NotImplementedError(
        "skip_trace_lead is not yet implemented. "
        "Open stubs/skip_trace.py and add your provider call."
    )
