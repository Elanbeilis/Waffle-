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

## ⚠️ TCPA / Legal Notice — Read Before Sending

> Cold SMS to property owners whose contact info was sourced from **public filing
> records** (lis pendens, notice of default, notice of sale) carries significant
> **TCPA exposure**.
>
> - TCPA statutory damages: **$500–$1,500 per unsolicited text**
> - Automated / pre-recorded messages to cell phones require **prior express written
>   consent** in most circumstances
> - "Investor-to-owner" texts are **not** categorically exempt
> - At any real volume, **A2P 10DLC registration** is mandatory for Twilio business SMS
>
> This tool never sends automatically — every message (AI-drafted or manual) lands in a
> **Pending Approvals queue** and requires your explicit "Send Now" click.
> The opt-out / DNC / quiet-hours gate is a compliance aid, not a guarantee.
> **Consult a licensed attorney before sending at scale. This is not legal advice.**

---

## Modules

| # | Module | Status |
|---|--------|--------|
| 1 | SQLite schema · FastAPI skeleton · CRUD | ✅ |
| 2 | CSV import wizard with per-source column mapping | ✅ |
| 3 | Pipeline dashboard · lead detail page | ✅ |
| 4 | Skip trace queue · manual entry · provider stub | ✅ |
| 5 | Lead scoring 0–100 · per-component breakdown · startup rescore | ✅ |
| 6 | Claude AI drafting → pending approval queue | ✅ |
| 7 | Twilio SMS · SendGrid email · compliance gate | ✅ |
| 8 | Public records scraper stub (Nassau · Suffolk · Kings) | ✅ stub |

---

## Stubs to implement

**`stubs/public_records_scraper.py`** — one class per county:
```python
class NassauScraper:
    def fetch_new_filings(self, county: str, since_date: date) -> list[ScrapedLead]:
        # POST to https://i2f.uslandrecords.com/NY/Nassau/, parse HTML
        ...
```
Once implemented, use the **Public Records** tab to fetch and import filings.

**`stubs/skip_trace.py`** — wire in BatchSkipTracing / TLOxp / IDI:
```python
def skip_trace_lead(lead_id: int) -> SkipTraceResult:
    info = get_lead_info(lead_id)
    # call your provider API
    ...
```
See the docstring in that file for a complete `httpx` skeleton.

---

## Lead Scoring

| Component | Max | Rule |
|---|---|---|
| Base | +50 | Always |
| Filing recency | +20 | Full 20 if filed today; −5 pts/month |
| Absentee owner | +15 | Mailing address ≠ property address |
| Equity | +10 | ≥$100k=10 · ≥$50k=5 · ≥$20k=2 |
| Idle decay | −20 | −2 pts/week after 7-day grace |

`POST /api/leads/rescore-all` or the **Rescore All** button refreshes all scores.
Scores are also refreshed on every server startup.

---

## Key API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leads` | List/filter leads |
| `POST` | `/api/leads` | Create lead (409 on duplicate) |
| `PATCH` | `/api/leads/{id}` | Update fields |
| `GET` | `/api/leads/stats` | Pipeline counts · overdue · DNC |
| `POST` | `/api/leads/rescore-all` | Recalculate all scores |
| `GET` | `/api/leads/{id}/score-breakdown` | Per-component score |
| `POST` | `/api/leads/{id}/draft-message` | AI-draft → pending queue |
| `POST` | `/api/outreach/{log_id}/send` | Send via Twilio/SendGrid (compliance-gated) |
| `GET` | `/api/outreach/pending` | Pending approval queue |
| `POST` | `/api/scrape/fetch` | Trigger county scrape |
| `GET` | `/api/scrape/counties` | Counties + implementation status |
| `POST` | `/api/import/upload` | Upload CSV (returns mapping session) |
| `POST` | `/api/import/execute` | Run import from mapped session |
| `GET` | `/api/skip-trace/queue` | Leads needing skip trace |
| `POST` | `/api/skip-trace/{id}/trigger` | Run provider (501 until stub implemented) |

Interactive docs: **http://localhost:8000/docs**

---

## Environment Variables

See `.env.example`. **Never commit `.env`.**

| Variable | Used by | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI drafting | — |
| `TWILIO_ACCOUNT_SID` | SMS | — |
| `TWILIO_AUTH_TOKEN` | SMS | — |
| `TWILIO_FROM_NUMBER` | SMS | — |
| `SENDGRID_API_KEY` | Email | — |
| `EMAIL_FROM` | Email | `noreply@example.com` |
| `SMS_QUIET_HOUR_START` | Compliance gate | `21` |
| `SMS_QUIET_HOUR_END` | Compliance gate | `8` |
| `LOCAL_TIMEZONE` | Compliance gate | `America/New_York` |

Missing credentials return a `503` with setup instructions — the app runs without them.
