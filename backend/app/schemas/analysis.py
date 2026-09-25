import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AnalysisRunResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    run_type: str
    status: str
    results_json: dict[str, Any] | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class DateRangeFilter(BaseModel):
    start: str | None = None
    end: str | None = None


class EdaFilterRequest(BaseModel):
    categorical_filters: dict[str, list[str]] = {}
    date_filters: dict[str, DateRangeFilter] = {}
