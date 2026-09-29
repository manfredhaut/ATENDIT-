#!/usr/bin/env python3
"""Cria backend/app/models/media.py (tabela operational_media) e registra
o import em models/__init__.py. A tabela e criada automaticamente no
proximo boot da API via create_all(checkfirst=True) -- sem migracao
manual. Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ARQ_MODELO = RAIZ / "backend/app/models/media.py"
ARQ_INIT = RAIZ / "backend/app/models/__init__.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)

CONTEUDO_MODELO = '''import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OperationalMedia(Base):
    """Midia operacional (imagens/videos) enviada pelo tenant para uso
    rapido durante atendimentos no WhatsApp -- isolada do RAG, sem nenhum
    RAGChunk ou vetorizacao associada."""

    __tablename__ = "operational_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
'''

if ARQ_MODELO.exists():
    print("[media.py] ja existe -- nada a fazer.")
else:
    ARQ_MODELO.write_text(CONTEUDO_MODELO, encoding="utf-8")
    print("[media.py] criado.")

txt_init = ARQ_INIT.read_text(encoding="utf-8")
LINHA_IMPORT = "from app.models.media import OperationalMedia\n"
if LINHA_IMPORT in txt_init:
    print("[__init__.py] ja registrado -- nada a fazer.")
else:
    ANCORA = "from app.models.catalog import Product\n"
    if ANCORA not in txt_init:
        sys.exit("ERRO: ancora 'from app.models.catalog import Product' nao encontrada em models/__init__.py.")
    novo_init = txt_init.replace(ANCORA, ANCORA + LINHA_IMPORT, 1)
    shutil.copy2(ARQ_INIT, bak / f"models_init.py.{CARIMBO}")
    ARQ_INIT.write_text(novo_init, encoding="utf-8")
    print("[__init__.py] import adicionado.")
