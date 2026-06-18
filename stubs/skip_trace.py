"""
Skip trace stub.

Wire in your chosen provider (BatchSkipTracing, TLOxp, IDI, etc.) by
implementing the body of `skip_trace_lead`.  The function must return a
SkipTraceResult.  The calling code in routers/leads.py will apply the result
to the lead and advance the pipeline stage automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
    # Raw response blob from the provider for debugging
    raw_response: dict = field(default_factory=dict)
    error: str | None = None


def skip_trace_lead(lead_id: int) -> SkipTraceResult:
    """
    Look up contact info for a lead.

    Steps to implement:
      1. Fetch the lead from the DB (import get_db / Lead as needed).
      2. Build the provider request payload (first name, last name, property address).
      3. POST to your provider's API (use SKIP_TRACE_API_KEY from env).
      4. Parse the response into phones/emails.
      5. Assess match quality (high = exact name+address match, etc.).
      6. Return SkipTraceResult.

    The calling code will:
      - Merge phones/emails onto the lead (union, no duplicates).
      - Set skip_trace_status = "complete" (or "failed" on error).
      - Advance pipeline_stage new → skip_traced if still at "new".

    Example provider docs:
      - BatchSkipTracing: https://batchskiptracing.com/api-docs
      - TLOxp:            https://tloxp.com/api
      - IDI:              https://www.ididata.com/
    """
    # TODO: implement provider call
    raise NotImplementedError(
        "skip_trace_lead is not yet implemented. "
        "Wire in your provider or use the manual skip-trace form on the lead detail page."
    )
