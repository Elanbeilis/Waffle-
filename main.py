from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import FilingType, LeadSource, OutreachChannel, PipelineStage, SkipTraceStatus
from routers import import_csv, leads, outreach


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Waffle RE Lead Gen", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(leads.router, prefix="/api")
app.include_router(outreach.router, prefix="/api")
app.include_router(import_csv.router, prefix="/api")


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
