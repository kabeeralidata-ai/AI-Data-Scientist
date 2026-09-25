import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func, JSON
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("datasets.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)  # eda | cleaning
    status: Mapped[str] = mapped_column(String(50), default="completed")
    results_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    dataset = relationship("Dataset", back_populates="analysis_runs")
