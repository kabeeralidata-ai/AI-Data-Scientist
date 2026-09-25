import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DatasetColumnResponse(BaseModel):
    name: str
    dtype: str
    semantic_type: Optional[str] = None
    missing_count: int
    unique_count: int
    is_numeric: bool
    is_categorical: bool
    is_datetime: bool
    is_id_like: bool = False

    class Config:
        from_attributes = True


class DatasetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    file_name: str
    file_type: str
    file_size: int
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    missing_values: Optional[int] = None
    duplicate_rows: Optional[int] = None
    is_cleaned: bool
    cleaning_version: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class TargetCandidateResponse(BaseModel):
    column: str
    score: float
    reason: str
    problem_type_hint: str


class DatasetDetailResponse(DatasetResponse):
    profile_json: Optional[dict[str, Any]] = None
    cleaning_log_json: Optional[dict[str, Any]] = None
    columns: list[DatasetColumnResponse] = []
    suggested_target_column: Optional[str] = None
    target_candidates: list[TargetCandidateResponse] = []


class CleaningRequest(BaseModel):
    missing_strategy: str = "auto"  # auto | drop | mean | median | mode | constant
    constant_value: Optional[str] = None
    remove_duplicates: bool = True
    columns: Optional[list[str]] = None


class QualityIssueResponse(BaseModel):
    id: str
    column: Optional[str] = None
    type: str
    severity: str
    message: str
    affected_count: int
    recommended_fix: dict[str, Any]


class DatasetRowResponse(BaseModel):
    row_index: int
    values: dict[str, Any]
