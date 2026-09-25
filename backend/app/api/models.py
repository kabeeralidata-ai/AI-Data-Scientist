import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.models.project import ProjectStatus
from app.models.user import User
from app.schemas.model import ModelDetailResponse, ModelResponse, TrainRequest
from app.services import ml_service
from app.utils.validators import DatasetValidationError

router = APIRouter(prefix="/api/models", tags=["models"])


def get_owned_model(db: Session, model_id: uuid.UUID, user: User) -> MLModel:
    model = db.query(MLModel).filter(MLModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")
    get_owned_project(db, model.project_id, user)
    return model


@router.post("/train", response_model=list[ModelResponse], status_code=201)
def train(payload: TrainRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset = db.query(Dataset).filter(Dataset.id == payload.dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    project = get_owned_project(db, dataset.project_id, current_user)

    try:
        trained = ml_service.train_models(
            db,
            project.id,
            dataset,
            payload.target_column,
            payload.feature_columns,
            payload.test_size,
            payload.random_seed,
            payload.models,
            cv_folds=payload.cv_folds,
            include_post_outcome_features=payload.include_post_outcome_features,
        )
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    project.target_column = payload.target_column
    project.status = ProjectStatus.COMPLETED
    db.commit()
    return trained


@router.get("/project/{project_id}", response_model=list[ModelResponse])
def list_project_models(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, current_user)
    return (
        db.query(MLModel)
        .filter(MLModel.project_id == project_id)
        .order_by(MLModel.created_at.desc())
        .all()
    )


@router.get("/{model_id}", response_model=ModelDetailResponse)
def get_model(model_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_owned_model(db, model_id, current_user)
