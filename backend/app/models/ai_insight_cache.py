import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import GUID


class AIInsightCache(Base):
    """Persists the last successfully-generated AI Insight per (project, dataset
    cleaning version, model, analysis kind) combination, so a page reload can show the
    existing insight without spending another Gemini request — only an explicit
    'Regenerate' (or a genuinely new dataset/model version) triggers a fresh call. This
    covers all three analysis types (supervised model, clustering, transaction-log), not
    just the supervised path. See context_service.compute_insight_cache_key for how
    cache_key is derived."""

    __tablename__ = "ai_insight_cache"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True)
    model_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    insight: Mapped[str] = mapped_column(Text, nullable=False)
    ai_available: Mapped[bool] = mapped_column(Boolean, default=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
