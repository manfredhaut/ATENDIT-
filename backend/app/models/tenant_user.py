"""
Conta de acesso do CLIENTE (tenant) ao painel dele.

Separada de `admin_users` de propósito: são populações diferentes, com
poderes diferentes. `admin_users` opera **todos** os inquilinos; um
`tenant_users` só existe dentro de um inquilino e cai junto com ele
(`ondelete="CASCADE"`).

Espelha o padrão que já funciona em `admin_users` — bcrypt via
`panel_auth.gerar_hash`/`conferir_hash`, e-mail normalizado em minúsculas na
escrita — em vez de inventar um segundo mecanismo de senha.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TenantUser(Base):
    __tablename__ = "tenant_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # UNIQUE global, nao por inquilino: o login e so o e-mail, entao o mesmo
    # endereco em dois inquilinos tornaria a autenticacao ambigua -- teriamos
    # de perguntar "qual empresa?" antes da senha, que e pior de usar e pior
    # de proteger.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="dono")

    # Tokens em coluna, nao assinados sem estado: o requisito e poder
    # INVALIDAR o token depois do uso, e token stateless so morre no
    # vencimento -- um link de reset vazado continuaria valendo pela hora
    # inteira mesmo depois de a senha ja ter sido trocada.
    token_verificacao: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    token_reset: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    token_reset_expira_em: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant = relationship("Tenant", lazy="selectin")
