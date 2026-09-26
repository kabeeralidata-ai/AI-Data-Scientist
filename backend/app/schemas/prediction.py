import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PredictionRequest(BaseModel):
    model_id: uuid.UUID
    features: dict[str, Any]


class PredictionResponse(BaseModel):
    id: uuid.UUID
    model_id: uuid.UUID
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class BatchPredictionResponse(BaseModel):
    model_id: uuid.UUID
    row_count: int
    results: list[dict[str, Any]]


class ReturnPredictionsResponse(BaseModel):
    available: bool
    reason: str | None = None
    window_days: int | None = None
    total_customers: int | None = None
    predicted_will_return: int | None = None
    predicted_will_not_return: int | None = None
    predicted_uncertain: int | None = None
    risk_bands: dict[str, str] | None = None
    risk_group_counts: dict[str, int] | None = None
    holdout_metrics: dict[str, Any] | None = None
    customers: list[dict[str, Any]] = []
