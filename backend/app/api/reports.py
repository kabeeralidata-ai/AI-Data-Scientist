import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.models import get_owned_model
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.dataset import Dataset
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportGenerateRequest, ReportResponse
from app.services.report_service import generate_report, get_report_preview_html

router = APIRouter(prefix="/api/reports", tags=["reports"])


def get_owned_report(db: Session, report_id: uuid.UUID, user: User) -> Report:
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    get_owned_project(db, report.project_id, user)
    return report


@router.post("/projects/{project_id}/generate", response_model=ReportResponse, status_code=201)
def create_report(
    project_id: uuid.UUID,
    payload: ReportGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_owned_project(db, project_id, current_user)

    dataset = None
    if payload.dataset_id:
        dataset = db.query(Dataset).filter(Dataset.id == payload.dataset_id).first()
        if not dataset or dataset.project_id != project.id:
            raise HTTPException(status_code=404, detail="Dataset not found.")
    elif project.datasets:
        dataset = sorted(project.datasets, key=lambda d: d.created_at)[-1]

    model = None
    if payload.model_id:
        model = get_owned_model(db, payload.model_id, current_user)
    elif project.models:
        best = [m for m in project.models if m.is_best]
        model = best[-1] if best else sorted(project.models, key=lambda m: m.created_at)[-1]

    return generate_report(db, project, dataset, model, payload.title, current_user)


@router.get("/projects/{project_id}", response_model=list[ReportResponse])
def list_reports(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, current_user)
    return (
        db.query(Report)
        .filter(Report.project_id == project_id)
        .order_by(Report.created_at.desc())
        .all()
    )


@router.get("/{report_id}", response_model=ReportResponse)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_owned_report(db, report_id, current_user)


@router.get("/{report_id}/download")
def download_report(report_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    report = get_owned_report(db, report_id, current_user)
    if not report.report_path:
        raise HTTPException(status_code=404, detail="Report file not found.")
    return FileResponse(report.report_path, media_type="application/pdf", filename=f"{report.title}.pdf")


@router.get("/{report_id}/preview", response_class=HTMLResponse)
def preview_report(report_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns the exact HTML that produced this report's PDF — same sections, charts,
    numbers, tables, and AI insights, since both were rendered from the same context."""
    report = get_owned_report(db, report_id, current_user)
    try:
        return HTMLResponse(content=get_report_preview_html(report))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
