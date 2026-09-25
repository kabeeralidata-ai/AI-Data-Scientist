import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func, Integer, BigInteger, JSON
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("projects.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_storage_path: Mapped[str] = mapped_column(String(1000), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int] = mapped_column(Integer, nullable=True)
    missing_values: Mapped[int] = mapped_column(Integer, nullable=True)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=True)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    is_cleaned: Mapped[bool] = mapped_column(default=False)
    cleaning_version: Mapped[int] = mapped_column(Integer, default=0)
    cleaning_log_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="datasets")
    columns = relationship("DatasetColumn", back_populates="dataset", cascade="all, delete-orphan")
    analysis_runs = relationship("AnalysisRun", back_populates="dataset", cascade="all, delete-orphan")
    models = relationship("MLModel", back_populates="dataset", cascade="all, delete-orphan")
