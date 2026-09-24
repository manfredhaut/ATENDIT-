"""
Schema do CRM de agendamento.

DECISÃO SOBRE ENUMS: todos usam Enum(..., native_enum=False), que gera
VARCHAR + CHECK constraint em vez de um tipo ENUM nativo do Postgres.
Motivo: tipo nativo exige ALTER TYPE para ganhar um valor novo, e este
projeto não tem migrações rodando (create_all com checkfirst só cria tabela
que falta — nunca altera tabela existente). Com CHECK, acrescentar um status
novo é uma migração de constraint, não de tipo, e o custo de errar é menor.

ARMADILHA HERDADA, VÁLIDA AQUI TAMBÉM: create_all(checkfirst=True) cria
apenas tabelas AUSENTES. Coluna nova em tabela que já existe NÃO é criada,
e o código novo falha em runtime procurando coluna que nunca nasceu. Toda
alteração de tabela existente é ALTER TABLE manual + ajuste no model.
"""

import uuid
from datetime import datetime, time
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# --- Vocabulários ---------------------------------------------------------
PROVEDORES_CALENDARIO = ("local", "google", "microsoft", "caldav", "calendly")
STATUS_AGENDAMENTO = ("confirmed", "cancelled", "completed", "no_show")
STATUS_ESPERA = ("waiting", "notified", "booked", "expired")
TIPOS_LEMBRETE = ("confirm", "reminder", "followup")
TIPOS_TEMPLATE = ("confirmation", "reminder", "waitlist_offer", "post_appointment")
# "calendly" entrou quando o adaptador passou a espelhar agendamentos feitos
# do lado de la. Enum e VARCHAR+CHECK (native_enum=False), entao acrescentar
# valor exige ALTER da CHECK no banco alem desta linha.
ORIGENS_AGENDAMENTO = ("whatsapp", "manual", "calendly")

# Padrões de negócio. Ficam como coluna (não constante) para serem ajustáveis
# por serviço sem deploy — ver PARTE 5.
TIMEOUT_LISTA_ESPERA_PADRAO = 15      # minutos para o candidato confirmar
ANTECEDENCIA_LEMBRETE_PADRAO = 1440   # minutos (24h) antes do compromisso


class ServiceType(Base):
    """Tipo de serviço oferecido pelo inquilino (ex.: consulta, avaliação)."""

    __tablename__ = "service_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Acrescentados ALÉM da especificação, para que a PARTE 5 seja configurável
    # por serviço em vez de depender de constante no código.
    waitlist_timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=TIMEOUT_LISTA_ESPERA_PADRAO
    )
    reminder_offset_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=ANTECEDENCIA_LEMBRETE_PADRAO
    )
    # Segundo lembrete, opcional (ex.: 24h antes E 2h antes).
    reminder_offset_minutes_2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    business_hours: Mapped[List["BusinessHours"]] = relationship(
        "BusinessHours", back_populates="service_type", cascade="all, delete-orphan"
    )
    appointments: Mapped[List["Appointment"]] = relationship("Appointment", back_populates="service_type")

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_service_types_duracao_positiva"),
        CheckConstraint("buffer_minutes >= 0", name="ck_service_types_buffer_nao_negativo"),
        Index("ix_service_types_tenant_ativo", "tenant_id", "is_active"),
    )


class BusinessHours(Base):
    """
    Janela de atendimento por dia da semana.

    service_type_id NULO significa 'vale para todos os serviços do inquilino' —
    é o caso comum (o negócio abre no mesmo horário para tudo). Preenchido,
    restringe àquele serviço.
    """

    __tablename__ = "business_hours"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_types.id", ondelete="CASCADE"), nullable=True, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=segunda ... 6=domingo
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    service_type: Mapped[Optional["ServiceType"]] = relationship("ServiceType", back_populates="business_hours")

    __table_args__ = (
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_business_hours_dia_valido"),
        CheckConstraint("end_time > start_time", name="ck_business_hours_fim_depois_inicio"),
        Index("ix_business_hours_tenant_dia", "tenant_id", "day_of_week", "is_active"),
    )


class CalendarConnection(Base):
    """
    Conexão com o calendário externo do inquilino.

    credentials_encrypted guarda o JSON de credenciais cifrado com Fernet
    (CALENDAR_ENCRYPTION_KEY). Perder essa chave torna TODAS as conexões
    ilegíveis e obriga cada inquilino a refazer o OAuth — ver RESTAURAR-AUTH.md.
    """

    __tablename__ = "calendar_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # unique=True saiu daqui: um inquilino pode ter Google E Calendly ao mesmo
    # tempo. A unicidade agora e do PAR (tenant_id, provider), no __table_args__.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(
        Enum(*PROVEDORES_CALENDARIO, name="provedor_calendario", native_enum=False),
        nullable=False, default="local",
    )
    credentials_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    calendar_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sync_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_calendar_connections_tenant_provider"),
    )


class Appointment(Base):
    """Compromisso agendado."""

    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*STATUS_AGENDAMENTO, name="status_agendamento", native_enum=False),
        nullable=False, default="confirmed", index=True,
    )
    provider: Mapped[str] = mapped_column(
        Enum(*PROVEDORES_CALENDARIO, name="provedor_calendario", native_enum=False),
        nullable=False, default="local",
    )
    external_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(
        Enum(*ORIGENS_AGENDAMENTO, name="origem_agendamento", native_enum=False),
        nullable=False, default="whatsapp",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    service_type: Mapped["ServiceType"] = relationship("ServiceType", back_populates="appointments")
    reminders: Mapped[List["ReminderLog"]] = relationship(
        "ReminderLog", back_populates="appointment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_appointments_fim_depois_inicio"),
        # Índice composto que serve a consulta mais frequente do motor:
        # "o que este inquilino tem marcado nesta faixa, ainda válido".
        Index("ix_appointments_tenant_inicio_status", "tenant_id", "start_at", "status"),
    )


class Waitlist(Base):
    """Fila de espera por uma janela desejada."""

    __tablename__ = "waitlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    desired_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    desired_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(
        Enum(*STATUS_ESPERA, name="status_espera", native_enum=False),
        nullable=False, default="waiting", index=True,
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Horario concretamente OFERTADO (diferente de desired_*, que e a janela
    # desejada). Sem gravar qual horario foi ofertado, um "sim" solto depois
    # nao teria a que se referir.
    offered_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    offered_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("desired_end > desired_start", name="ck_waitlist_fim_depois_inicio"),
        # priority ASC = mais prioritário primeiro; empate resolvido por
        # desired_start, e depois por created_at (quem entrou antes).
        Index("ix_waitlist_tenant_status_prioridade", "tenant_id", "status", "priority", "desired_start"),
    )


class ReminderLog(Base):
    """
    Registro de lembretes enviados.

    Existe para IDEMPOTÊNCIA: a task do Beat roda a cada 5 minutos e
    reencontraria os mesmos compromissos. A restrição única
    (appointment_id, reminder_type, offset_minutes) é o que impede o cliente
    de receber o mesmo lembrete doze vezes por hora.
    """

    __tablename__ = "reminder_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reminder_type: Mapped[str] = mapped_column(
        Enum(*TIPOS_LEMBRETE, name="tipo_lembrete", native_enum=False), nullable=False
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")

    appointment: Mapped["Appointment"] = relationship("Appointment", back_populates="reminders")

    __table_args__ = (
        UniqueConstraint(
            "appointment_id", "reminder_type", "offset_minutes",
            name="uq_reminder_log_compromisso_tipo_offset",
        ),
    )


class MessageTemplate(Base):
    """
    Régua de mensagem por inquilino e tipo.

    A ausência de linha NÃO é erro: significa "usa o default do código".
    Por isso não há seed — um inquilino novo já funciona sem nenhuma linha
    aqui, e a tela só grava quando alguém customiza de fato.
    """

    __tablename__ = "message_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_type: Mapped[str] = mapped_column(
        Enum(*TIPOS_TEMPLATE, name="tipo_template", native_enum=False), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "template_type", name="uq_message_templates_tenant_tipo"),
    )


class Lead(Base):
    """
    Lead capturado via formulário público, landing page ou canais omnichannel.
    """

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    whatsapp: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comentario: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origem: Mapped[str] = mapped_column(String(40), nullable=False, default="landing")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="novo", index=True)
    temperatura: Mapped[str] = mapped_column(String(20), nullable=False, default="morno", index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    intencao: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resumo_ia: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prioridade: Mapped[str] = mapped_column(String(20), nullable=False, default="media", index=True)
    boas_vindas_enviada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    boas_vindas_enviada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_leads_created_at", "created_at"),)
