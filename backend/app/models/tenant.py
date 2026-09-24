import uuid
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.rag import RAGDocument


class Tenant(Base):
    """Modelo relacional de Inquilino (Tenant) para arquitetura Multi-tenant."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    admin_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trial_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Vinculo com o gateway WhatsApp (Evolution API) ---
    # Indexados: o webhook resolve o tenant pela instancia que recebeu a mensagem.
    evolution_instance: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, index=True, nullable=True
    )
    whatsapp_jid: Mapped[Optional[str]] = mapped_column(
        String(50), index=True, nullable=True
    )
    whatsapp_number_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    whatsapp_notificacoes: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    profile_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    meta_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relacionamento 1:1 com a configuração de IA
    ai_config: Mapped[Optional["AIConfig"]] = relationship(
        "AIConfig",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Relacionamento 1:N com os documentos RAG
    documents: Mapped[List["RAGDocument"]] = relationship(
        "RAGDocument",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AIConfig(Base):
    """Modelo relacional para configuração de IA e credenciais do Tenant."""

    __tablename__ = "ai_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), default="gemini", nullable=False)
    api_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(100), default="ManiBot", nullable=False)
    system_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(100), default="gemini-1.5-flash", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meta_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relacionamento reverso com o Tenant
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="ai_config")
