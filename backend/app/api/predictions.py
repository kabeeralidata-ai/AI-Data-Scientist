import io
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.models import get_owned_model
from app.api.projects import get_owned_project
from app.core.database import get_db
from app.models.user import User
from app.schemas.prediction import (
    BatchPredictionResponse,
    PredictionRequest,
    PredictionResponse,
    ReturnPredictionsResponse,
)
from app.services import auto_analyze_service, prediction_service
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


@router.get("/return-predictions/{project_id}", response_model=ReturnPredictionsResponse)
def get_return_predictions(
    project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Per-customer return predictions for a transaction-log project — probability and
    risk group for every identified customer. Transaction-log projects never train an
    MLModel row (Finding 21), so this reads straight from the latest completed Auto
    Analyze job instead of the model-based prediction endpoints above."""
    get_owned_project(db, project_id, current_user)
    return_prediction = auto_analyze_service.get_latest_return_predictions(db, project_id)
    if not return_prediction:
        return ReturnPredictionsResponse(available=False, reason="No return-prediction analysis has completed for this project yet.")

    return ReturnPredictionsResponse(
        available=True,
        window_days=return_prediction["window_days"],
        total_customers=return_prediction["total_customers"],
        predicted_will_return=return_prediction["predicted_will_return"],
        predicted_will_not_return=return_prediction["predicted_will_not_return"],
        predicted_uncertain=return_prediction["predicted_uncertain"],
        risk_bands=return_prediction["risk_bands"],
        risk_group_counts=return_prediction["risk_group_counts"],
        holdout_metrics=return_prediction["holdout_metrics"],
        customers=return_prediction["predictions"],
    )


@router.get("/return-predictions/{project_id}/download")
def download_return_predictions(
    project_id: uuid.UUID,
    format: str = "csv",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_owned_project(db, project_id, current_user)
    return_prediction = auto_analyze_service.get_latest_return_predictions(db, project_id)
    if not return_prediction:
        raise HTTPException(status_code=404, detail="No return-prediction analysis has completed for this project yet.")

    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=422, detail="format must be 'csv' or 'xlsx'.")

    customers_df = pd.DataFrame(return_prediction["predictions"])
    if format == "csv":
        buffer = io.StringIO()
        customers_df.to_csv(buffer, index=False)
        content = buffer.getvalue().encode("utf-8")
        media_type = "text/csv"
        filename = "return_predictions.csv"
    else:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            customers_df.to_excel(writer, index=False, sheet_name="Return Predictions")
        content = buffer.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "return_predictions.xlsx"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
