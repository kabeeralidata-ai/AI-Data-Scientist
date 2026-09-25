import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, DateTime, ForeignKey, func, JSON, Text
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AutoAnalyzeJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_TARGET_CONFIRMATION = "awaiting_target_confirmation"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class AutoAnalyzeJob(Base):
    __tablename__ = "auto_analyze_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default=AutoAnalyzeJobStatus.PENDING)
    current_step: Mapped[str] = mapped_column(String(100), nullable=True)
    steps_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    target_candidates_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    confirmed_target_column: Mapped[str] = mapped_column(String(255), nullable=True)
    # Set by the "Change target" flow — when true, always re-prompt for target
    # confirmation even if the project has a remembered target from a previous run.
    force_target_reselection: Mapped[bool] = mapped_column(Boolean, default=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project = relationship("Project")
    dataset = relationship("Dataset")
