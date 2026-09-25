import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TrainRequest(BaseModel):
    dataset_id: uuid.UUID
    target_column: str
    feature_columns: Optional[list[str]] = None
    test_size: float = 0.2
    cv_folds: int = Field(default=5, ge=2, le=10)
    random_seed: int = 42
    models: Optional[list[str]] = None  # subset of available model types
    # Post-outcome-named features are excluded by default; a column listed here is
    # explicitly re-included despite the suspicion (the "Retrain including selected" flow).
    include_post_outcome_features: Optional[list[str]] = None


class ModelResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    model_type: str
    problem_type: str
    target_column: str
    feature_columns_json: Optional[list[str]] = None
    metrics_json: Optional[dict[str, Any]] = None
    feature_importance_json: Optional[list[dict[str, Any]]] = None
    is_best: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ModelDetailResponse(ModelResponse):
    preprocessing_json: Optional[dict[str, Any]] = None
    hyperparameters_json: Optional[dict[str, Any]] = None
    comparison_json: Optional[dict[str, Any]] = None
    feature_schema_json: Optional[list[dict[str, Any]]] = None
    random_seed: int
