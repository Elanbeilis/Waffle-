import csv
import io
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import FilingType, Lead, LeadSource, SkipTraceStatus
from routers.leads import _find_duplicate, _make_dedup_key
from scoring import calculate_score

router = APIRouter(prefix="/import", tags=["import"])

MAPPINGS_DIR = Path("column_mappings")
MAPPINGS_DIR.mkdir(exist_ok=True)

# Session store: {session_id: {"filepath": str, "headers": list, "row_count": int, ...}}
_sessions: dict[str, dict] = {}

# Human-readable descriptions for the mapping UI
IMPORTABLE_FIELDS: dict[str, str] = {
    "address":          "Property address (required)",
    "city":             "City",
    "county":           "County",
    "owner_names":      "Owner name(s) — split on comma / & / AND",
    "mailing_address":  "Mailing / absentee address",
    "filing_type":      "Filing type (lis pendens / NOD / NOS / etc.)",
    "filing_date":      "Filing date",
    "estimated_equity": "Estimated equity ($)",
    "phones":           "Phone number(s)",
    "emails":           "Email address(es)",
    "notes":            "Notes / remarks",
}

# Per-source default header guesses (PropStream / PropertyShark common exports)
SOURCE_HINTS: dict[str, dict[str, str]] = {
    "propstream": {
        "address":          "Property Address",
        "city":             "City",
        "county":           "County",
        "owner_names":      "Owner Name",
        "mailing_address":  "Mailing Address",
        "filing_date":      "Filing Date",
        "estimated_equity": "Equity Estimate",
        "phones":           "Phone",
        "emails":           "Email",
    },
    "propertyshark": {
        "address":          "Address",
        "city":             "City",
        "county":           "County Name",
        "owner_names":      "Owner",
        "mailing_address":  "Owner Mailing Address",
        "filing_date":      "Recording Date",
        "estimated_equity": "Estimated Equity",
        "phones":           "Phone Number",
        "emails":           "Email",
    },
}

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
    "%Y-%m-%dT%H:%M:%S", "%m-%d-%Y", "%d/%m/%Y",
]

FILING_TYPE_MAP: dict[str, FilingType] = {
    "lis pendens":        FilingType.lis_pendens,
    "lis_pendens":        FilingType.lis_pendens,
    "lp":                 FilingType.lis_pendens,
    "notice of default":  FilingType.notice_of_default,
    "notice_of_default":  FilingType.notice_of_default,
    "nod":                FilingType.notice_of_default,
    "foreclosure":        FilingType.notice_of_default,
    "default":            FilingType.notice_of_default,
    "notice of sale":     FilingType.notice_of_sale,
    "notice_of_sale":     FilingType.notice_of_sale,
    "nos":                FilingType.notice_of_sale,
    "standard listing":   FilingType.standard_listing,
    "standard_listing":   FilingType.standard_listing,
    "listing":            FilingType.standard_listing,
    "mls":                FilingType.standard_listing,
    "fsbo":               FilingType.fsbo,
    "for sale by owner":  FilingType.fsbo,
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_date(val: str) -> Optional[datetime]:
    if not val:
        return None
    val = val.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _parse_equity(val: str) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[$,\s]", "", val.strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_list(val: str) -> list[str]:
    """Split multi-value fields (owners, phones, emails)."""
    if not val:
        return []
    parts = re.split(r"[;]|,\s*|\s+AND\s+|\s*&\s*", val, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _normalize_phone(val: str) -> str:
    digits = re.sub(r"\D", "", val)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return val.strip()


def _parse_filing_type(val: str) -> FilingType:
    if not val:
        return FilingType.none
    return FILING_TYPE_MAP.get(val.lower().strip(), FilingType.none)


def _get_col(row: dict, col: Any) -> str:
    """Get value from row for a mapping value (str or list of str)."""
    if not col:
        return ""
    if isinstance(col, list):
        return " ".join(str(row.get(c, "") or "") for c in col).strip()
    return str(row.get(col, "") or "").strip()


# ---------------------------------------------------------------------------
# Mapping persistence
# ---------------------------------------------------------------------------

def _load_mapping(source: str) -> Optional[dict]:
    path = MAPPINGS_DIR / f"{source}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _save_mapping(source: str, mappings: dict) -> None:
    path = MAPPINGS_DIR / f"{source}.json"
    path.write_text(json.dumps({
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mappings": mappings,
    }, indent=2))


def _suggest_mapping(headers: list[str], source: str) -> dict[str, str]:
    """Best-effort column guess: source hints first, then fuzzy fallback."""
    result: dict[str, str] = {}
    header_set = set(headers)
    header_lower = {h.lower(): h for h in headers}

    # Source-specific hints
    for field, hint in SOURCE_HINTS.get(source, {}).items():
        if hint in header_set:
            result[field] = hint

    # Fuzzy fallback for unmatched fields
    for field in IMPORTABLE_FIELDS:
        if field in result:
            continue
        candidates = [
            field.replace("_", " "),
            field.replace("_", ""),
            field,
        ]
        for c in candidates:
            if c in header_lower:
                result[field] = header_lower[c]
                break

    return result


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class MappingConfig(BaseModel):
    source: str
    mappings: dict[str, Any]  # field -> column name or list of column names


class ExecuteImportRequest(BaseModel):
    session_id: str
    source: str
    mappings: dict[str, Any]
    save_mapping: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(...),
    source: str = Form("manual"),
):
    """Step 1: receive CSV, return headers + sample rows + suggested mapping."""
    raw = await file.read()
    # Handle UTF-8 BOM (common in Windows CSV exports)
    text = raw.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    if not headers:
        raise HTTPException(status_code=400, detail="CSV has no headers or is empty.")

    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV has headers but no data rows.")

    # Persist temp file for the execute step
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".csv", mode="w", encoding="utf-8", newline=""
    )
    tmp.write(text)
    tmp.close()

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "filepath": tmp.name,
        "headers": headers,
        "row_count": len(rows),
        "source": source,
        "filename": file.filename or "upload.csv",
    }

    # Build suggested mapping (saved mapping takes priority over auto-suggest)
    saved = _load_mapping(source)
    suggested = _suggest_mapping(headers, source)
    active_mapping = (saved or {}).get("mappings", suggested)

    return {
        "session_id": session_id,
        "filename": file.filename,
        "row_count": len(rows),
        "headers": headers,
        "sample_rows": [dict(r) for r in rows[:5]],
        "saved_mapping": (saved or {}).get("mappings"),
        "suggested_mapping": active_mapping,
        "field_descriptions": IMPORTABLE_FIELDS,
    }


@router.get("/mapping/{source}")
def get_mapping(source: str):
    """Return the saved column mapping for a source, if any."""
    mapping = _load_mapping(source)
    if not mapping:
        return {"source": source, "mappings": None}
    return mapping


@router.post("/mapping/{source}")
def save_mapping_endpoint(source: str, body: MappingConfig):
    """Explicitly save a column mapping for a source."""
    _save_mapping(source, body.mappings)
    return {"saved": True, "source": source}


@router.post("/execute")
def execute_import(body: ExecuteImportRequest, db: Session = Depends(get_db)):
    """Step 2: run the import using a confirmed column mapping."""
    session = _sessions.get(body.session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Import session not found or expired. Re-upload the CSV.",
        )

    filepath = session["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Temp file missing. Re-upload the CSV.")

    if body.save_mapping and body.mappings:
        _save_mapping(body.source, body.mappings)

    results = {
        "total_rows": 0,
        "imported": 0,
        "skipped_duplicates": 0,
        "errors": 0,
        "duplicate_details": [],
        "error_details": [],
        "imported_lead_ids": [],
    }

    source_enum = LeadSource(body.source) if body.source in LeadSource.__members__ else LeadSource.manual
    m = body.mappings

    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):  # 2 = first data row
            results["total_rows"] += 1
            try:
                address = _get_col(row, m.get("address", ""))
                if not address:
                    results["errors"] += 1
                    results["error_details"].append({
                        "row": row_num, "error": "Missing required field: address",
                        "raw": dict(row),
                    })
                    continue

                owner_raw = _get_col(row, m.get("owner_names", ""))
                owner_names = _parse_list(owner_raw) if owner_raw else []

                dup = _find_duplicate(db, address, owner_names)
                if dup:
                    results["skipped_duplicates"] += 1
                    results["duplicate_details"].append({
                        "row": row_num,
                        "address": address,
                        "owner_names": owner_names,
                        "existing_lead_id": dup.id,
                    })
                    continue

                # Parse equity
                equity_raw = _get_col(row, m.get("estimated_equity", ""))
                equity = _parse_equity(equity_raw)

                # Parse phones / emails
                phones_raw = _get_col(row, m.get("phones", ""))
                phones = [_normalize_phone(p) for p in _parse_list(phones_raw)]

                emails_raw = _get_col(row, m.get("emails", ""))
                emails = [e.lower() for e in _parse_list(emails_raw)]

                lead = Lead(
                    address=address,
                    city=_get_col(row, m.get("city", "")) or None,
                    county=_get_col(row, m.get("county", "")) or None,
                    owner_names=owner_names or None,
                    mailing_address=_get_col(row, m.get("mailing_address", "")) or None,
                    lead_source=source_enum,
                    filing_type=_parse_filing_type(_get_col(row, m.get("filing_type", ""))),
                    filing_date=_parse_date(_get_col(row, m.get("filing_date", ""))),
                    estimated_equity=equity,
                    phones=phones or None,
                    emails=emails or None,
                    notes=_get_col(row, m.get("notes", "")) or None,
                    skip_trace_status=SkipTraceStatus.not_needed,
                    dedup_key=_make_dedup_key(address, owner_names),
                )
                lead.score = calculate_score(lead)
                db.add(lead)
                db.flush()  # get ID before commit
                results["imported_lead_ids"].append(lead.id)
                results["imported"] += 1

            except Exception as exc:
                results["errors"] += 1
                results["error_details"].append({
                    "row": row_num, "error": str(exc), "raw": dict(row),
                })

    db.commit()

    # Clean up temp file
    try:
        os.unlink(filepath)
    except OSError:
        pass
    del _sessions[body.session_id]

    return results
