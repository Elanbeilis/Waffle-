# Waffle RE Lead Gen

Local real estate wholesaling lead-gen and outreach tool.  
Single-user, runs on your machine — no deployment, no auth.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys as you wire each module
uvicorn main:app --reload
```

Open **http://localhost:8000** in your browser.  
API docs: **http://localhost:8000/docs**

## Modules

| # | Module | Status |
|---|--------|--------|
| 1 | SQLite schema + FastAPI skeleton + CRUD | ✅ Done |
| 2 | CSV import with column mapping | 🔜 Next |
| 3 | Dashboard pipeline + lead detail view | 🔜 |
| 4 | Skip trace stub + manual entry form | 🔜 |
| 5 | Scoring function | ✅ Implemented in `scoring.py` |
| 6 | Claude AI drafting + approval queue | 🔜 |
| 7 | Twilio/SendGrid send + compliance gate | 🔜 |
| 8 | Public records scraper stub | ✅ Stub in `stubs/public_records_scraper.py` |

## Stubs to implement yourself

- **`stubs/public_records_scraper.py`** — county-specific scraping logic
  (Nassau, Suffolk, Kings/ACRIS stubs are already scaffolded).
- **`stubs/skip_trace.py`** — wire in BatchSkipTracing / TLOxp / IDI.

## ⚠️ TCPA / Legal notice

Cold SMS outreach to property owners sourced from public filings
(lis pendens, NOD, NOS) carries **TCPA exposure**.  These contacts have
no prior consent or established business relationship with you.  
At any real send volume, **A2P 10DLC registration** is required for
Twilio business SMS.  The opt-out / quiet-hours logic in this tool is
a compliance aid — it does **not** constitute legal compliance on its own.
Consult a telemarketing / real-estate attorney before sending at scale.
*This is not legal advice.*

## Environment variables

See `.env.example`.  Never commit `.env`.
