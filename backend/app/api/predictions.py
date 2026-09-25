import io
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.models import get_owned_model
from app.core.database import get_db
from app.models.user import User
from app.schemas.prediction import BatchPredictionResponse, PredictionRequest, PredictionResponse
from app.services import prediction_service
from app.utils.validators import DatasetValidationError

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.post("", response_model=PredictionResponse, status_code=201)
def create_prediction(payload: PredictionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    model = get_owned_model(db, payload.model_id, current_user)
    try:
        return prediction_service.predict(db, model, payload.features)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/batch", response_model=BatchPredictionResponse)
async def create_batch_prediction(
    model_id: uuid.UUID = Form(...),
    file: UploadFile | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    model = get_owned_model(db, model_id, current_user)
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file was provided.")
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Please upload a .csv, .xlsx, or .xls file.")

    file_bytes = await file.read()
    try:
        if file.filename.lower().endswith(".csv"):
            rows_df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            rows_df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="We could not read this file. Please verify it is a valid CSV or Excel file.",
        )

    if rows_df.shape[0] == 0:
        raise HTTPException(status_code=422, detail="The uploaded file does not contain any data rows.")

    try:
        results = prediction_service.predict_batch(model, rows_df)
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return BatchPredictionResponse(model_id=model.id, row_count=len(results), results=results)
