import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.datasets import get_owned_dataset
from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.auto_analyze import AutoAnalyzeJobResponse, AutoAnalyzeRequest, ConfirmPlanRequest, ConfirmTargetRequest
from app.services import auto_analyze_service

router = APIRouter(prefix="/api", tags=["auto-analyze"])


@router.post("/projects/{project_id}/auto-analyze", response_model=AutoAnalyzeJobResponse, status_code=201)
def start_auto_analyze(
    project_id: uuid.UUID,
    payload: AutoAnalyzeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(db, project_id, current_user)
    dataset = get_owned_dataset(db, payload.dataset_id, current_user)
    if dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Dataset not found in this project.")

    job = auto_analyze_service.create_job(
        db, project_id, payload.dataset_id, force_target_reselection=payload.force_target_reselection
    )
    background_tasks.add_task(auto_analyze_service.run_auto_analyze, job.id)
    return job


@router.get("/auto-analyze/{job_id}", response_model=AutoAnalyzeJobResponse)
def get_auto_analyze_status(job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Auto Analyze job not found.")
    get_owned_project(db, job.project_id, current_user)
    return job


@router.get("/projects/{project_id}/auto-analyze/latest", response_model=AutoAnalyzeJobResponse | None)
def get_latest_auto_analyze_job(
    project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    get_owned_project(db, project_id, current_user)
    return (
        db.query(AutoAnalyzeJob)
        .filter(AutoAnalyzeJob.project_id == project_id)
        .order_by(AutoAnalyzeJob.created_at.desc())
        .first()
    )


def _get_owned_job(db: Session, job_id: uuid.UUID, user: User) -> AutoAnalyzeJob:
    job = db.query(AutoAnalyzeJob).filter(AutoAnalyzeJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Auto Analyze job not found.")
    get_owned_project(db, job.project_id, user)
    return job


@router.post("/auto-analyze/{job_id}/confirm-target", response_model=AutoAnalyzeJobResponse)
def confirm_target(
    job_id: uuid.UUID,
    payload: ConfirmTargetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _get_owned_job(db, job_id, current_user)
    if job.status != AutoAnalyzeJobStatus.AWAITING_TARGET_CONFIRMATION:
        raise HTTPException(status_code=409, detail="This job is not awaiting target confirmation.")

    dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
    valid_columns = {c["name"] for c in (dataset.profile_json or {}).get("columns", [])} if dataset else set()
    if payload.target_column not in valid_columns:
        raise HTTPException(status_code=422, detail="The selected target column does not exist in this dataset.")

    # confirm_target_and_resume itself transitions the status away from
    # AWAITING_TARGET_CONFIRMATION once it actually starts — flipping it here first would
    # make the background task's own status guard see a mismatch and no-op.
    background_tasks.add_task(auto_analyze_service.confirm_target_and_resume, job.id, payload.target_column)
    return job


@router.post("/auto-analyze/{job_id}/confirm-plan", response_model=AutoAnalyzeJobResponse)
def confirm_plan(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    payload: ConfirmPlanRequest = ConfirmPlanRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirms the analysis plan shown to the user (transaction-log revenue/RFM/returns/
    forecasting, or segmentation) and starts running it — or, if target_column is given,
    overrides the plan entirely and runs supervised training on that column instead."""
    job = _get_owned_job(db, job_id, current_user)
    if job.status != AutoAnalyzeJobStatus.AWAITING_PLAN_CONFIRMATION:
        raise HTTPException(status_code=409, detail="This job is not awaiting plan confirmation.")

    if payload.target_column is not None:
        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        valid_columns = {c["name"] for c in (dataset.profile_json or {}).get("columns", [])} if dataset else set()
        if payload.target_column not in valid_columns:
            raise HTTPException(status_code=422, detail="The selected target column does not exist in this dataset.")

    background_tasks.add_task(auto_analyze_service.confirm_plan_and_resume, job.id, payload.target_column)
    return job


@router.post("/auto-analyze/{job_id}/retry-ai-insights", response_model=AutoAnalyzeJobResponse)
def retry_ai_insights(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _get_owned_job(db, job_id, current_user)
    if job.status != AutoAnalyzeJobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="AI insights can only be retried on a completed job.")

    background_tasks.add_task(auto_analyze_service.retry_ai_insights, job.id)
    return job
