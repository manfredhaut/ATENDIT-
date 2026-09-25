import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Product(Base):
    """Catálogo unificado de produtos e procedimentos/serviços (F4.1)."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="product")  # 'product' ou 'service'
    sku: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=30)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # 'active' ou 'draft'
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
