import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.models import get_owned_model
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus
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


def _get_latest_completed_job_result(db: Session, project_id: uuid.UUID, dataset_id: uuid.UUID | None) -> dict:
    """Returns the result_json of the most recent completed Auto Analyze job for this
    project (optionally filtered to a specific dataset), so the report-generation endpoint
    can include clustering results or transaction-analysis data that live only on the job —
    never on an MLModel row.

    This is the root-cause fix for Phase 9 Finding A: a project with multiple completed
    Auto Analyze runs (e.g. one general analysis that completed before clustering finished,
    then a second confirmed-plan run that produced k=4 clusters) would previously generate
    a report without ANY cluster data because the API endpoint only looked at project.models
    — which is always empty for clustering/transaction-log projects."""
    q = (
        db.query(AutoAnalyzeJob)
        .filter(
            AutoAnalyzeJob.project_id == project_id,
            AutoAnalyzeJob.status == AutoAnalyzeJobStatus.COMPLETED,
        )
    )
    if dataset_id:
        q = q.filter(AutoAnalyzeJob.dataset_id == dataset_id)
    latest_job = q.order_by(AutoAnalyzeJob.updated_at.desc()).first()
    return (latest_job.result_json or {}) if latest_job else {}


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

    # For clustering and transaction-log projects the model is always None (no MLModel
    # row is ever saved for these analysis types). Recover clusters / transaction_analysis
    # from the most recent completed Auto Analyze job so the re-generated report uses the
    # real analysis result rather than an empty "no model" placeholder.
    clusters = None
    transaction_analysis = None
    no_model_reason = None
    if model is None:
        dataset_id_for_lookup = dataset.id if dataset else None
        job_result = _get_latest_completed_job_result(db, project.id, dataset_id_for_lookup)
        analysis_type = job_result.get("analysis_type")
        if analysis_type == "clustering":
            clusters = job_result.get("clusters")
            no_model_reason = (
                "No target column was confidently detected in this dataset, so no supervised model was trained."
            )
        elif analysis_type == "transaction_log":
            transaction_analysis = job_result.get("transaction_analysis")

    return generate_report(
        db,
        project,
        dataset,
        model,
        payload.title,
        current_user,
        clusters=clusters,
        no_model_reason=no_model_reason,
        transaction_analysis=transaction_analysis,
    )


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
