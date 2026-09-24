import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

class LogisticConfig(Base):
    """Parametrização de atendimento logístico e Cal.com por inquilino."""
    __tablename__ = "logistic_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    cal_api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cal_event_slug: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cal_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="headless")
    base_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    base_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    base_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    radius_km: Mapped[int] = mapped_column(Integer, nullable=False, default=35)
    buffer_traffic: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    remind_24h: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    remind_2h: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

class ServiceOrder(Base):
    """Ordem de serviço de visita técnica vinculada a agendamento."""
    __tablename__ = "logistic_service_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    destination_address: Mapped[str] = mapped_column(String(255), nullable=False)
    distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    travel_time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="scheduled")
    confirmation_24h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmation_2h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_service_orders_tenant_status", "tenant_id", "status"),
    )
