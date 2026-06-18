"""
Public records scraper stub.

Implement one function per county.  Each implementation should:
  1. Hit the county clerk / court records portal for the given county.
  2. Parse new filings since `since_date`.
  3. Return a list of LeadCreate-compatible dicts (or LeadCreate objects).

Supported counties to implement: Nassau, Suffolk, Kings (Brooklyn).
Every county has a different URL structure and auth pattern — fill in the
county-specific logic below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class ScrapedLead:
    """Minimal lead data returned from a public records scrape."""
    address: str
    city: str
    county: str
    owner_names: list[str] = field(default_factory=list)
    mailing_address: str | None = None
    filing_type: str = "none"          # use FilingType enum values
    filing_date: date | None = None
    estimated_equity: float | None = None
    notes: str = ""


class CountyScraper(Protocol):
    """Interface every county scraper must satisfy."""

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        ...


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_new_filings(county: str, since_date: date) -> list[ScrapedLead]:
    """
    Dispatch to the right county scraper.

    Usage:
        from stubs.public_records_scraper import fetch_new_filings
        leads = fetch_new_filings("nassau", date(2024, 1, 1))
    """
    county_lower = county.lower().replace(" ", "_")

    scrapers: dict[str, CountyScraper] = {
        "nassau": NassauScraper(),
        "suffolk": SuffolkScraper(),
        "kings": KingsScraper(),
    }

    scraper = scrapers.get(county_lower)
    if scraper is None:
        raise NotImplementedError(
            f"No scraper implemented for county '{county}'. "
            f"Available: {list(scrapers.keys())}"
        )

    return scraper.fetch_new_filings(county, since_date)


# ---------------------------------------------------------------------------
# Per-county implementations (fill in)
# ---------------------------------------------------------------------------

class NassauScraper:
    """Nassau County, NY — https://i2f.uslandrecords.com/NY/Nassau/"""

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        # TODO: implement Nassau scraper
        # Typical approach: POST search form, parse HTML table, extract leads
        raise NotImplementedError("Nassau scraper not yet implemented")


class SuffolkScraper:
    """Suffolk County, NY — https://i2f.uslandrecords.com/NY/Suffolk/"""

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        # TODO: implement Suffolk scraper
        raise NotImplementedError("Suffolk scraper not yet implemented")


class KingsScraper:
    """Kings County (Brooklyn), NY — https://www.acris.nyc.gov/"""

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        # TODO: implement Kings/ACRIS scraper
        # ACRIS has a public REST API: https://data.cityofnewyork.us/resource/...
        raise NotImplementedError("Kings (ACRIS) scraper not yet implemented")
