"""
Lead scoring — 0 to 100.

Formula (max 100 pts):
  Base                  50   always
  Filing recency     +0-20   full pts if filed today, decays 5 pts/month
  Absentee owner       +15   mailing_address ≠ property address
  Estimated equity    +0-10   tiered: ≥$100k=10, ≥$50k=5, ≥$20k=2
  Idle decay         -0-20   leads untouched >7 days lose 2 pts/week

Use score_breakdown() to get the per-component explanation.
Use calculate_score() when you only need the final number.
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


def score_breakdown(lead: Lead) -> dict:
    """
    Returns the score and a per-component breakdown.

    Shape:
      {
        "total": 73.5,
        "components": {
          "base":           {"points": 50.0, "max": 50,  "reason": "..."},
          "filing_recency": {"points": 12.5, "max": 20,  "reason": "..."},
          "absentee_owner": {"points": 15.0, "max": 15,  "reason": "..."},
          "equity":         {"points":  5.0, "max": 10,  "reason": "..."},
          "idle_decay":     {"points": -9.0, "max":  0,  "reason": "..."},
        }
      }
    """
    components: dict[str, dict] = {}
    running = 0.0

    # ── Base ─────────────────────────────────────────────────────────────
    components["base"] = {"points": 50.0, "max": 50, "reason": "Base score"}
    running += 50.0

    # ── Filing recency ────────────────────────────────────────────────────
    days_filed = _days_ago(lead.filing_date)
    if days_filed is not None:
        pts = round(max(0.0, 20.0 - (days_filed / 30) * 5.0), 1)
        reason = (
            f"Filed {int(days_filed)}d ago — "
            f"{pts:.1f} pts (full 20 if <30d, −5 pts per month after)"
        )
    else:
        pts = 0.0
        reason = "No filing date on record"
    components["filing_recency"] = {"points": pts, "max": 20, "reason": reason}
    running += pts

    # ── Absentee owner ────────────────────────────────────────────────────
    if lead.mailing_address and lead.address:
        if _normalize(lead.mailing_address) != _normalize(lead.address):
            components["absentee_owner"] = {
                "points": 15.0, "max": 15,
                "reason": "Mailing address differs from property — likely absentee owner",
            }
            running += 15.0
        else:
            components["absentee_owner"] = {
                "points": 0.0, "max": 15,
                "reason": "Mailing address matches property — owner-occupied",
            }
    else:
        components["absentee_owner"] = {
            "points": 0.0, "max": 15,
            "reason": "No mailing address on record — cannot assess absentee status",
        }

    # ── Equity ────────────────────────────────────────────────────────────
    eq = lead.estimated_equity or 0
    if eq >= 100_000:
        e_pts, e_reason = 10.0, f"${eq:,.0f} equity — ≥$100k tier (+10)"
    elif eq >= 50_000:
        e_pts, e_reason = 5.0,  f"${eq:,.0f} equity — $50k–$100k tier (+5)"
    elif eq >= 20_000:
        e_pts, e_reason = 2.0,  f"${eq:,.0f} equity — $20k–$50k tier (+2)"
    else:
        e_pts, e_reason = 0.0,  "No equity data or below $20k threshold"
    components["equity"] = {"points": e_pts, "max": 10, "reason": e_reason}
    running += e_pts

    # ── Idle decay ────────────────────────────────────────────────────────
    # Clock starts from last_contact_date if set, else from created_at.
    idle_source = "last contact" if lead.last_contact_date else "created"
    idle_days = _days_ago(lead.last_contact_date) or _days_ago(lead.created_at) or 0
    grace = 7.0
    decay = round(max(0.0, min(20.0, (idle_days - grace) / 7.0 * 2.0)), 1)
    if idle_days <= grace:
        d_reason = f"{idle_days:.0f}d idle ({idle_source}) — within {int(grace)}-day grace window"
    else:
        d_reason = (
            f"{idle_days:.0f}d since {idle_source} — "
            f"−{decay} pts (−2 pts/week after {int(grace)}-day grace)"
        )
    components["idle_decay"] = {"points": -decay, "max": 0, "reason": d_reason}
    running -= decay

    final = round(max(0.0, min(100.0, running)), 1)
    return {"total": final, "components": components}


def calculate_score(lead: Lead) -> float:
    return score_breakdown(lead)["total"]
