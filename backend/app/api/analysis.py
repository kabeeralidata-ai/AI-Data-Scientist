import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.datasets import get_owned_dataset
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.analysis import EdaFilterRequest
from app.services import eda_service
from app.utils.validators import DatasetValidationError

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/{dataset_id}/eda")
def run_eda(dataset_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dataset = get_owned_dataset(db, dataset_id, current_user)
    try:
        return eda_service.run_eda(db, dataset)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{dataset_id}/eda/query")
def query_filtered_eda(
    dataset_id: uuid.UUID,
    payload: EdaFilterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The BI-dashboard EDA endpoint — auto-selected KPIs/charts, filtered and
    aggregated entirely server-side with pandas (only aggregated JSON is ever returned,
    never raw rows), briefly cached per exact filter combination."""
    dataset = get_owned_dataset(db, dataset_id, current_user)
    date_filters = {col: rng.model_dump() for col, rng in payload.date_filters.items()}
    try:
        return eda_service.run_filtered_eda(
            db, dataset, categorical_filters=payload.categorical_filters, date_filters=date_filters
        )
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
