import uuid

from sqlalchemy import String, ForeignKey, Integer, Boolean
from app.core.types import GUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("datasets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dtype: Mapped[str] = mapped_column(String(50), nullable=False)
    semantic_type: Mapped[str] = mapped_column(String(50), nullable=True)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    is_numeric: Mapped[bool] = mapped_column(Boolean, default=False)
    is_categorical: Mapped[bool] = mapped_column(Boolean, default=False)
    is_datetime: Mapped[bool] = mapped_column(Boolean, default=False)
    is_id_like: Mapped[bool] = mapped_column(Boolean, default=False)

    dataset = relationship("Dataset", back_populates="columns")
