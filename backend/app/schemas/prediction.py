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
