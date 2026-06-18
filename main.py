from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import FilingType, LeadSource, OutreachChannel, PipelineStage, SkipTraceStatus
from routers import drafting, import_csv, leads, outreach, scraper, sending, skip_trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Refresh scores on startup so idle-decay penalties reflect the current date
    from database import SessionLocal
    from models import Lead as _Lead
    from scoring import calculate_score as _calc
    _db = SessionLocal()
    try:
        _leads = _db.query(_Lead).all()
        for _lead in _leads:
            _new = _calc(_lead)
            if abs((_lead.score or 0.0) - _new) >= 0.05:
                _lead.score = _new
        _db.commit()
    finally:
        _db.close()
    yield


app = FastAPI(title="Waffle RE Lead Gen", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(leads.router, prefix="/api")
app.include_router(outreach.router, prefix="/api")
app.include_router(sending.router, prefix="/api")
app.include_router(drafting.router, prefix="/api")
app.include_router(import_csv.router, prefix="/api")
app.include_router(skip_trace.router, prefix="/api")
app.include_router(scraper.router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/lead/{lead_id}", response_class=HTMLResponse)
async def lead_detail(request: Request, lead_id: int, db: Session = Depends(get_db)):
    from models import Lead
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead #{lead_id} not found")
    return templates.TemplateResponse(request, "lead_detail.html", {
        "lead": lead,
        "outreach_logs": lead.outreach_logs,
        "pipeline_stages": [s.value for s in PipelineStage],
        "filing_types": [t.value for t in FilingType],
        "lead_sources": [s.value for s in LeadSource],
        "skip_trace_statuses": [s.value for s in SkipTraceStatus],
        "channels": [c.value for c in OutreachChannel],
    })
