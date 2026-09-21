"""
Usuários do painel administrativo.

Substitui a comparação contra ADMIN_PASSWORD do .env. A senha nunca é
guardada aqui — só o hash bcrypt, que é one-way e tem salt embutido em
cada linha (dois usuários com a mesma senha geram hashes diferentes).

Preparado para multi-tenant: quando o painel deixar de ser de um operador
só, entra uma coluna tenant_id e um vínculo N:N, sem mexer no mecanismo de
autenticação.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    # citext seria melhor, mas exigiria extensao; normalizamos para minusculas
    # na escrita e na leitura, que resolve o mesmo problema sem dependencia.
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
