import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.dataset import (
    CleaningRequest,
    DatasetDetailResponse,
    DatasetResponse,
    DatasetRowResponse,
    QualityIssueResponse,
    TargetCandidateResponse,
)
from app.services import cleaning_service, data_understanding_service, dataset_service
from app.utils.validators import DatasetValidationError

router = APIRouter(prefix="/api", tags=["datasets"])

SAMPLE_DATASET_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "sample_customer_churn.csv")


def get_owned_dataset(db: Session, dataset_id: uuid.UUID, user: User) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    get_owned_project(db, dataset.project_id, user)
    return dataset


def _to_detail_response(dataset: Dataset) -> DatasetDetailResponse:
    response = DatasetDetailResponse.model_validate(dataset)
    if dataset.profile_json:
        description = dataset.project.description if dataset.project else None
        candidates = dataset_service.score_target_candidates(dataset.profile_json, description)
        response.target_candidates = [TargetCandidateResponse(**c) for c in candidates]
        response.suggested_target_column = candidates[0]["column"] if candidates else dataset_service.suggest_target_column(
            dataset.profile_json, description
        )
    return response


@router.post("/projects/{project_id}/datasets/upload", response_model=DatasetResponse, status_code=201)
async def upload_dataset(
    project_id: uuid.UUID,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(db, project_id, current_user)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file was provided.")

    file_bytes = await file.read()
    try:
        dataset = dataset_service.save_upload(db, project_id, file.filename, file_bytes)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return dataset


@router.post("/projects/{project_id}/datasets/sample", response_model=DatasetResponse, status_code=201)
def upload_sample_dataset(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lets a new user try the platform instantly with a real, bundled sample dataset."""
    get_owned_project(db, project_id, current_user)

    if not os.path.exists(SAMPLE_DATASET_PATH):
        raise HTTPException(status_code=500, detail="The sample dataset is not available on this server.")

    with open(SAMPLE_DATASET_PATH, "rb") as f:
        file_bytes = f.read()

    try:
        dataset = dataset_service.save_upload(db, project_id, "sample_customer_churn.csv", file_bytes)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return dataset


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetResponse])
def list_datasets(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, current_user)
    return (
        db.query(Dataset)
        .filter(Dataset.project_id == project_id)
        .order_by(Dataset.created_at.desc())
        .all()
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailResponse)
def get_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset = get_owned_dataset(db, dataset_id, current_user)
    return _to_detail_response(dataset)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset = get_owned_dataset(db, dataset_id, current_user)
    for path in {dataset.storage_path, dataset.original_storage_path}:
        if path and os.path.exists(path):
            os.remove(path)
    db.delete(dataset)
    db.commit()


@router.get("/datasets/{dataset_id}/quality", response_model=list[QualityIssueResponse])
def get_dataset_quality(dataset_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset = get_owned_dataset(db, dataset_id, current_user)
    try:
        column_roles = data_understanding_service.understand_dataset(dataset)["column_roles"]
        return cleaning_service.analyze_quality(dataset, column_roles=column_roles)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/datasets/{dataset_id}/rows", response_model=list[DatasetRowResponse])
def get_dataset_rows(
    dataset_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Backs the 'Load from Dataset' picker in the prediction form."""
    dataset = get_owned_dataset(db, dataset_id, current_user)
    try:
        return dataset_service.get_sample_rows(dataset, limit=limit, search=search)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/analysis/{dataset_id}/clean", response_model=DatasetDetailResponse)
def clean_dataset(
    dataset_id: uuid.UUID,
    payload: CleaningRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dataset = get_owned_dataset(db, dataset_id, current_user)
    try:
        column_roles = data_understanding_service.understand_dataset(dataset)["column_roles"]
        cleaned = cleaning_service.clean_dataset(db, dataset, payload, column_roles=column_roles)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _to_detail_response(cleaned)
