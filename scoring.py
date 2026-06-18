"""
Lead scoring (0–100).  Called on every lead create/update.

Formula breakdown (max 100 pts):
  - Base                        50
  - Filing recency              up to +20  (full pts if filed <30 days ago, decays ~5pts/month)
  - Absentee owner              +15        (mailing_address != property address)
  - Estimated equity            up to +10  (tiered: $100k+ / $50k+ / $20k+)
  - Time-in-pipeline decay      up to -20  (leads untouched >7 days start losing pts)
"""

from datetime import datetime, timezone

from models import Lead


def _days_ago(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def calculate_score(lead: Lead) -> float:
    score = 50.0

    # Filing recency
    days_filed = _days_ago(lead.filing_date)
    if days_filed is not None:
        recency = max(0.0, 20.0 - (days_filed / 30) * 5.0)
        score += recency

    # Absentee owner
    if lead.mailing_address and lead.address:
        if _normalize(lead.mailing_address) != _normalize(lead.address):
            score += 15.0

    # Estimated equity
    eq = lead.estimated_equity or 0
    if eq >= 100_000:
        score += 10.0
    elif eq >= 50_000:
        score += 5.0
    elif eq >= 20_000:
        score += 2.0

    # Time-in-pipeline decay (based on last contact, fallback to created_at)
    idle_days = _days_ago(lead.last_contact_date) or _days_ago(lead.created_at) or 0
    # First 7 days: no decay.  Then lose up to 20 pts (2 pts/week).
    decay = max(0.0, min(20.0, (idle_days - 7) / 7 * 2.0))
    score -= decay

    return round(max(0.0, min(100.0, score)), 1)
