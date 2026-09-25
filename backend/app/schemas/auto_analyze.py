import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator


class AutoAnalyzeRequest(BaseModel):
    dataset_id: uuid.UUID
    # "Change target" flow: always re-prompt for target confirmation, ignoring any
    # remembered target from a previous run on this project.
    force_target_reselection: bool = False


class AutoAnalyzeStep(BaseModel):
    key: str
    label: str
    status: str  # pending | running | completed | warning | skipped | failed
    detail: Optional[str] = None


class TargetCandidate(BaseModel):
    column: str
    score: float
    reason: str
    problem_type_hint: str


class ConfirmTargetRequest(BaseModel):
    target_column: str


class AutoAnalyzeJobResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    current_step: Optional[str] = None
    steps_json: list[AutoAnalyzeStep] = []
    target_candidates_json: list[TargetCandidate] = []
    confirmed_target_column: Optional[str] = None
    result_json: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    @field_validator("target_candidates_json", "steps_json", mode="before")
    @classmethod
    def _default_empty_list(cls, v):
        return v or []
