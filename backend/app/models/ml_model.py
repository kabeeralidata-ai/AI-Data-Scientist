import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func, JSON, Integer
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MLModel(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("projects.id"), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    model_type: Mapped[str] = mapped_column(String(100), nullable=False)
    problem_type: Mapped[str] = mapped_column(String(50), nullable=False)  # classification | regression
    target_column: Mapped[str] = mapped_column(String(255), nullable=False)
    feature_columns_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    preprocessing_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    hyperparameters_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    feature_importance_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    feature_schema_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    comparison_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    is_best: Mapped[bool] = mapped_column(default=False)
    random_seed: Mapped[int] = mapped_column(Integer, default=42)
    model_path: Mapped[str] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="models")
    dataset = relationship("Dataset", back_populates="models")
    predictions = relationship("Prediction", back_populates="model", cascade="all, delete-orphan")
