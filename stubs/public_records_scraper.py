"""
Public records scraper — NY county lis pendens / notice of default filings.

Kings County (Brooklyn) — FULLY IMPLEMENTED via ACRIS / NYC Open Data REST API.
  No credentials required.  Optional: set NYC_OPEN_DATA_APP_TOKEN in .env for
  higher rate limits (free token at https://data.cityofnewyork.us/profile/app_tokens).

Nassau County — SKELETON only.  The i2f.uslandrecords.com portal has no public
  API; it uses an ASPX form.  Fill in the two TODO sections after a one-time
  browser-inspection session (see instructions inside NassauScraper).

Suffolk County — same situation as Nassau (same i2f portal software).
"""

from __future__ import annotations

import os
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
    filing_type: str = "none"       # FilingType enum values
    filing_date: date | None = None
    estimated_equity: float | None = None
    notes: str = ""


class CountyScraper(Protocol):
    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]: ...


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_new_filings(county: str, since_date: date) -> list[ScrapedLead]:
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
# Helper
# ---------------------------------------------------------------------------

def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ---------------------------------------------------------------------------
# Kings County (Brooklyn) — ACRIS / NYC Open Data REST API
# ---------------------------------------------------------------------------

class KingsScraper:
    """
    Kings County (Brooklyn), NY.

    Data source: ACRIS via NYC Open Data (Socrata REST API).
    No credentials required; set NYC_OPEN_DATA_APP_TOKEN in .env for higher
    rate limits (free, takes 30 seconds to register).

    ACRIS document types fetched:
      LP  = Lis Pendens
      AGMT = Agreement (catches some pre-foreclosure arrangements)

    Borough code 3 = Brooklyn.
    """

    BOROUGH = "3"
    MASTER_URL  = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"
    LEGALS_URL  = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
    PARTIES_URL = "https://data.cityofnewyork.us/resource/636b-3b5g.json"
    DOC_TYPES   = {"LP": "lis_pendens", "AGMT": "none"}

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        import httpx

        token = os.getenv("NYC_OPEN_DATA_APP_TOKEN", "")
        headers = {"X-App-Token": token} if token else {}

        # ── Step 1: fetch LP documents from Real Property Master ─────────────
        doc_types_sql = ",".join(f"'{t}'" for t in self.DOC_TYPES)
        master_params = {
            "$where": (
                f"document_type IN({doc_types_sql})"
                f" AND borough='{self.BOROUGH}'"
                f" AND filed_date>='{since_date.isoformat()}'"
            ),
            "$select": "document_id,document_type,filed_date",
            "$order":  "filed_date DESC",
            "$limit":  "5000",
        }
        with httpx.Client(timeout=30) as client:
            r = client.get(self.MASTER_URL, params=master_params, headers=headers)
            r.raise_for_status()
            masters = r.json()

        if not masters:
            return []

        doc_ids   = [m["document_id"] for m in masters]
        master_by = {m["document_id"]: m for m in masters}

        # ── Step 2: fetch property addresses from Real Property Legals ────────
        legals_by: dict[str, dict] = {}
        for chunk in _chunks(doc_ids, 100):
            ids_in = ",".join(f"'{d}'" for d in chunk)
            params = {
                "$where":  f"document_id IN({ids_in})",
                "$select": "document_id,street_number,street_name,unit",
                "$limit":  str(len(chunk) * 3),
            }
            with httpx.Client(timeout=30) as client:
                r = client.get(self.LEGALS_URL, params=params, headers=headers)
                r.raise_for_status()
                for row in r.json():
                    # keep only the first legal row per document
                    legals_by.setdefault(row["document_id"], row)

        # ── Step 3: fetch owner names / mailing from Real Property Parties ────
        # party_type='1' = grantor / debtor
        parties_by: dict[str, list[dict]] = {}
        for chunk in _chunks(doc_ids, 100):
            ids_in = ",".join(f"'{d}'" for d in chunk)
            params = {
                "$where":  f"document_id IN({ids_in}) AND party_type='1'",
                "$select": "document_id,name,address_1,city,state,zip",
                "$limit":  str(len(chunk) * 4),
            }
            with httpx.Client(timeout=30) as client:
                r = client.get(self.PARTIES_URL, params=params, headers=headers)
                r.raise_for_status()
                for row in r.json():
                    parties_by.setdefault(row["document_id"], []).append(row)

        # ── Step 4: assemble ScrapedLead objects ──────────────────────────────
        results: list[ScrapedLead] = []
        for doc_id, master in master_by.items():
            legal = legals_by.get(doc_id)
            if not legal:
                continue

            street_num  = (legal.get("street_number") or "").strip()
            street_name = (legal.get("street_name")   or "").strip()
            unit        = (legal.get("unit")           or "").strip()
            address = f"{street_num} {street_name}".strip()
            if unit:
                address += f" Unit {unit}"
            if not address:
                continue

            parties = parties_by.get(doc_id, [])
            owner_names = [
                p["name"].title()
                for p in parties
                if p.get("name", "").strip()
            ]

            mailing = None
            if parties:
                p = parties[0]
                parts = [
                    (p.get("address_1") or "").strip(),
                    (p.get("city")      or "").strip(),
                    (p.get("state")     or "").strip(),
                    (p.get("zip")       or "").strip(),
                ]
                parts = [x for x in parts if x]
                if parts:
                    mailing = ", ".join(parts)

            filing_dt = None
            raw_date  = master.get("filed_date", "")
            if raw_date:
                try:
                    filing_dt = date.fromisoformat(raw_date[:10])
                except ValueError:
                    pass

            filing_type = self.DOC_TYPES.get(master.get("document_type", ""), "none")

            results.append(ScrapedLead(
                address=address,
                city="Brooklyn",
                county="Kings",
                owner_names=owner_names,
                mailing_address=mailing,
                filing_type=filing_type,
                filing_date=filing_dt,
            ))

        return results


# ---------------------------------------------------------------------------
# Nassau County — i2f.uslandrecords.com (ASPX form — needs 1-time inspection)
# ---------------------------------------------------------------------------

class NassauScraper:
    """
    Nassau County, NY — https://i2f.uslandrecords.com/NY/Nassau/

    Status: SKELETON.  The portal has no public REST API; it uses an ASP.NET
    WebForms (ASPX) session with ViewState.  Two one-time steps to complete:

      1. Open Chrome DevTools → Network tab on the portal search page.
      2. Run a lis pendens date-range search manually.
      3. Copy the POST request as cURL (right-click in Network tab → "Copy as cURL").
      4. Fill in TODO A and TODO B below with the real field names/values.

    Once those two sections are filled in this class will work for any date range.
    """

    BASE_URL   = "https://i2f.uslandrecords.com/NY/Nassau/D/Default.aspx"
    SEARCH_URL = "https://i2f.uslandrecords.com/NY/Nassau/D/DocumentSearch.aspx"

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        import httpx
        from html.parser import HTMLParser

        raise NotImplementedError(
            "Nassau scraper needs a one-time browser-inspection step — see the "
            "docstring in NassauScraper for instructions."
        )

        # ── TODO A: capture actual ASPX form field names from DevTools ────────
        # Replace the keys below with the real names you see in the POST body.
        #
        # with httpx.Client(timeout=30, follow_redirects=True) as client:
        #     # 1. GET the search page to obtain __VIEWSTATE / __EVENTVALIDATION
        #     page = client.get(self.SEARCH_URL)
        #     vs, ev = _parse_aspx_hidden(page.text)
        #
        #     # 2. POST the search form
        #     form_data = {
        #         "__VIEWSTATE":       vs,
        #         "__EVENTVALIDATION": ev,
        #         # TODO A — replace these keys with real field names from DevTools:
        #         "ctl00$cphMain$ddlDocType":  "LP",          # Lis Pendens code
        #         "ctl00$cphMain$txtDateFrom": since_date.strftime("%m/%d/%Y"),
        #         "ctl00$cphMain$txtDateTo":   date.today().strftime("%m/%d/%Y"),
        #         "ctl00$cphMain$btnSearch":   "Search",
        #     }
        #     results_page = client.post(self.SEARCH_URL, data=form_data)
        #
        # # TODO B — parse the HTML results table into ScrapedLead objects.
        # # Inspect the results page HTML to find the table/row structure, then:
        # # return _parse_i2f_results(results_page.text, "Nassau")

        return []


# ---------------------------------------------------------------------------
# Suffolk County — i2f.uslandrecords.com (same ASPX portal as Nassau)
# ---------------------------------------------------------------------------

class SuffolkScraper:
    """
    Suffolk County, NY — https://i2f.uslandrecords.com/NY/Suffolk/

    Status: SKELETON.  Same ASPX portal software as Nassau.
    Follow the same DevTools inspection steps documented in NassauScraper,
    then mirror the changes here (only the BASE_URL / SEARCH_URL differ).
    """

    BASE_URL   = "https://i2f.uslandrecords.com/NY/Suffolk/D/Default.aspx"
    SEARCH_URL = "https://i2f.uslandrecords.com/NY/Suffolk/D/DocumentSearch.aspx"

    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        raise NotImplementedError(
            "Suffolk scraper needs a one-time browser-inspection step — see the "
            "docstring in NassauScraper for instructions (same portal software)."
        )
        return []


# ---------------------------------------------------------------------------
# ASPX helpers (used by Nassau / Suffolk once skeleton is filled in)
# ---------------------------------------------------------------------------

def _parse_aspx_hidden(html: str) -> tuple[str, str]:
    """Extract __VIEWSTATE and __EVENTVALIDATION from an ASPX page."""
    from html.parser import HTMLParser

    fields: dict[str, str] = {}

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "input":
                d = dict(attrs)
                if d.get("name") in ("__VIEWSTATE", "__EVENTVALIDATION"):
                    fields[d["name"]] = d.get("value", "")

    _P().feed(html)
    return fields.get("__VIEWSTATE", ""), fields.get("__EVENTVALIDATION", "")
