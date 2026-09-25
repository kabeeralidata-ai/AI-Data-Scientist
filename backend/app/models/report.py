import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func, JSON
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("projects.id"), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    report_path: Mapped[str] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="reports")
