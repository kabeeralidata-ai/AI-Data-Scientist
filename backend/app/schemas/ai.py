import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InsightRequest(BaseModel):
    model_id: Optional[uuid.UUID] = None
    dataset_id: Optional[uuid.UUID] = None
    force: bool = False  # True = "Regenerate": bypass any cached insight and call Gemini fresh


class InsightResponse(BaseModel):
    insight: str
    ai_available: bool
    provider: Optional[str] = None
    source_context: dict
    generated_at: datetime
    cached: bool = False


class ChatMessageRequest(BaseModel):
    conversation_id: Optional[uuid.UUID] = None
    project_id: uuid.UUID
    message: str


class ChatMessageResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    ai_available: bool
    ai_generated: bool
    provider: Optional[str] = None


class AIMessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    ai_generated: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AIConversationResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime
    messages: list[AIMessageResponse] = []

    class Config:
        from_attributes = True
