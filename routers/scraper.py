"""
Public records scraper router.

POST /api/scrape/fetch  — calls the county stub, creates leads for new filings
GET  /api/scrape/counties — lists supported counties and implementation status

Scrapers themselves live in stubs/public_records_scraper.py.
Implement a county by filling in the stub class; this router picks it up
automatically on the next request.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import FilingType, Lead, LeadSource
from routers.leads import _make_dedup_key
from scoring import calculate_score

router = APIRouter(prefix="/scrape", tags=["public_records"])

_COUNTIES = [
    {
        "name": "nassau",
        "label": "Nassau County, NY",
        "url": "https://i2f.uslandrecords.com/NY/Nassau/",
        "scraper_class": "NassauScraper",
    },
    {
        "name": "suffolk",
        "label": "Suffolk County, NY",
        "url": "https://i2f.uslandrecords.com/NY/Suffolk/",
        "scraper_class": "SuffolkScraper",
    },
    {
        "name": "kings",
        "label": "Kings County (Brooklyn), NY",
        "url": "https://www.acris.nyc.gov/",
        "scraper_class": "KingsScraper",
    },
]


class FetchRequest(BaseModel):
    county: str
    since_date: Optional[date] = None   # defaults to 30 days ago
    dry_run: bool = False                # preview without writing to DB


class FetchResult(BaseModel):
    county: str
    since_date: date
    dry_run: bool = False
    created: int = 0
    duplicates: int = 0
    errors: int = 0
    leads_created: list[int] = []
    duplicate_details: list[dict] = []
    error_details: list[dict] = []


@router.get("/counties")
def list_counties():
    """List supported counties and whether their scraper is implemented."""
    from stubs.public_records_scraper import fetch_new_filings  # noqa
    results = []
    for c in _COUNTIES:
        try:
            # Probe: call with a tiny date range — NotImplementedError means not ready
            from stubs import public_records_scraper as _mod
            scraper_cls = getattr(_mod, c["scraper_class"])
            scraper_cls().fetch_new_filings(c["name"], date.today())
            implemented = True
        except NotImplementedError:
            implemented = False
        except Exception:
            implemented = False  # any other error = not usable yet
        results.append({**c, "implemented": implemented})
    return {"counties": results}


@router.post("/fetch", response_model=FetchResult)
def fetch_filings(payload: FetchRequest, db: Session = Depends(get_db)):
    """
    Trigger a county scrape and import new filings as leads.

    Deduplicates against existing leads using the same address+owner key as
    the manual create endpoint.  Dry-run mode reports counts without writing.
    """
    from stubs.public_records_scraper import fetch_new_filings

    since = payload.since_date or (date.today() - timedelta(days=30))
    county = payload.county.strip().lower().replace(" ", "_")

    try:
        scraped = fetch_new_filings(county, since)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "not_implemented": True,
                "county": payload.county,
                "message": str(exc),
                "instructions": (
                    f"Open stubs/public_records_scraper.py and implement "
                    f"{payload.county.capitalize()}Scraper.fetch_new_filings()."
                ),
            },
        )

    result = FetchResult(county=payload.county, since_date=since, dry_run=payload.dry_run)

    for idx, s in enumerate(scraped):
        try:
            key = _make_dedup_key(s.address, s.owner_names or [])
            existing = db.query(Lead).filter(Lead.dedup_key == key).first()
            if existing:
                result.duplicates += 1
                result.duplicate_details.append({
                    "address": s.address,
                    "owner_names": s.owner_names,
                    "existing_lead_id": existing.id,
                })
                continue

            result.created += 1
            if payload.dry_run:
                continue

            try:
                filing_type = FilingType(s.filing_type)
            except ValueError:
                filing_type = FilingType.none

            filing_dt = None
            if s.filing_date:
                filing_dt = datetime(
                    s.filing_date.year, s.filing_date.month, s.filing_date.day,
                    tzinfo=timezone.utc,
                )

            lead = Lead(
                address=s.address,
                city=s.city,
                county=s.county,
                owner_names=s.owner_names or [],
                mailing_address=s.mailing_address,
                lead_source=LeadSource.public_record,
                filing_type=filing_type,
                filing_date=filing_dt,
                estimated_equity=s.estimated_equity,
                notes=s.notes or None,
                dedup_key=key,
            )
            lead.score = calculate_score(lead)
            db.add(lead)
            db.flush()
            result.leads_created.append(lead.id)

        except Exception as exc:
            result.errors += 1
            result.error_details.append({
                "index": idx,
                "address": getattr(s, "address", "?"),
                "error": str(exc),
            })

    if not payload.dry_run and result.created > 0:
        db.commit()

    return result
