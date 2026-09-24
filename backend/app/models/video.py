import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class VideoRoom(Base):
    """
    Sala efêmera de videoconferência WebRTC para atendimento e Tour Guiado.
    Permite comunicação P2P direta com sinalização via WebSocket gerenciada pelo Traefik.
    """
    __tablename__ = "video_rooms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    customer_phone: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )
    customer_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="waiting", nullable=False  # waiting, active, completed, expired
    )
    tags_context: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    escalation_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant = relationship("Tenant")
