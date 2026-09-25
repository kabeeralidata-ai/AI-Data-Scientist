import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    dataset_id: Optional[uuid.UUID] = None
    model_id: Optional[uuid.UUID] = None
    title: Optional[str] = None


class ReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    content_json: Optional[dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True
