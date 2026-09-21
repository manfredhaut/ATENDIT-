import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import exigir_token_interno
from app.models.tenant import AIConfig, Tenant

logger = logging.getLogger("atendit.ai_config")

# Prefixos de chave que o Google efetivamente emite para a API Gemini.
PREFIXOS_CHAVE_GEMINI = ("AIzaSy", "AQ.")
COMPRIMENTO_MINIMO_CHAVE_GEMINI = 30

router = APIRouter(prefix="/ai-config", tags=["AI Provisioning"])


# --- SCHEMAS PYDANTIC ---
class AICustomKeySubmitSchema(BaseModel):
    provider: str = Field(default="gemini", description="Provedor do modelo de linguagem (ex: gemini, openai)")
    api_key: str = Field(..., min_length=8, max_length=255, description="Chave de API privada do provedor")
    agent_name: str = Field(default="ManiBot", max_length=100, description="Nome de exibição do atendente virtual")
    system_instruction: Optional[str] = Field(default=None, description="Instrução do sistema / persona do agente")
    model: str = Field(default="gemini-1.5-flash", max_length=100, description="Identificador do modelo LLM")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Parâmetro de temperatura para inferência")


class AIConfigResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    agent_name: str
    system_instruction: Optional[str]
    model: str
    temperature: float
    is_active: bool
    created_at: datetime
    updated_at: datetime
    has_custom_key: bool = False


# --- ROTAS ASSÍNCRONAS COM PERSISTÊNCIA NO POSTGRESQL ---
@router.post(
    "/submit/{tenant_id}",
    response_model=AIConfigResponseSchema,
    dependencies=[Depends(exigir_token_interno)],
)
async def submit_custom_ai_key(
    tenant_id: uuid.UUID,
    payload: AICustomKeySubmitSchema,
    db: AsyncSession = Depends(get_db)
):
    """
    Submete e ativa credencial personalizada de IA para um Tenant específico.
    Persiste as configurações de persona, modelo e chave de acesso no banco relacional.
    """
    logger.info(f"[AI PROVISIONING] Atualizando configuração de IA para o Tenant: {tenant_id}")

    # Validação básica de conformidade para chaves Google Gemini.
    # O Google emite MAIS DE UM formato: 'AIzaSy...' (39 chars) e 'AQ....' (53
    # chars). Exigir só 'AIzaSy' rejeitava chaves válidas - inclusive a que
    # estava em uso em produção. O árbitro final de uma chave é a resposta da
    # API (HTTP 200), não o prefixo; aqui só barramos o que é obviamente
    # malformado, para dar erro claro em vez de 400 vindo do Google.
    if payload.provider.lower() == "gemini":
        chave = (payload.api_key or "").strip()
        if len(chave) < COMPRIMENTO_MINIMO_CHAVE_GEMINI or not chave.startswith(PREFIXOS_CHAVE_GEMINI):
            logger.warning(
                f"[AI PROVISIONING] Chave Gemini malformada recebida para Tenant: {tenant_id} "
                f"(tamanho={len(chave)})"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Formato de chave do Google Gemini inválido. A chave deve começar com "
                    f"{' ou '.join(PREFIXOS_CHAVE_GEMINI)} e ter ao menos "
                    f"{COMPRIMENTO_MINIMO_CHAVE_GEMINI} caracteres."
                )
            )

    # Verifica se o tenant existe
    stmt_tenant = select(Tenant).where(Tenant.id == tenant_id)
    res_tenant = await db.execute(stmt_tenant)
    tenant = res_tenant.scalar_one_or_none()

    if not tenant:
        logger.warning(f"[AI PROVISIONING] Tenant não localizado: ID={tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant não localizado para vincular configuração de IA."
        )

    # Busca configuração de IA existente ou cria nova
    stmt_ai = select(AIConfig).where(AIConfig.tenant_id == tenant_id)
    res_ai = await db.execute(stmt_ai)
    ai_config = res_ai.scalar_one_or_none()

    if not ai_config:
        ai_config = AIConfig(tenant_id=tenant_id)
        db.add(ai_config)

    # Atualização dos parâmetros
    ai_config.provider = payload.provider.lower()
    ai_config.api_key = payload.api_key
    ai_config.agent_name = payload.agent_name
    ai_config.system_instruction = payload.system_instruction
    ai_config.model = payload.model
    ai_config.temperature = payload.temperature
    ai_config.is_active = True

    await db.flush()
    await db.refresh(ai_config)

    logger.info(f"[AI PROVISIONING] Configuração de IA homologada e persistida com sucesso para o Tenant: {tenant_id}")

    response = AIConfigResponseSchema.model_validate(ai_config)
    response.has_custom_key = bool(ai_config.api_key)
    return response


@router.get("/{tenant_id}", response_model=AIConfigResponseSchema)
async def get_tenant_ai_config(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Recupera a configuração atual de IA vinculada ao Tenant."""
    stmt = select(AIConfig).where(AIConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    ai_config = result.scalar_one_or_none()

    if not ai_config:
        logger.warning(f"[AI PROVISIONING] Configuração de IA não encontrada para o Tenant: {tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuração de IA não encontrada para este Tenant."
        )

    response = AIConfigResponseSchema.model_validate(ai_config)
    response.has_custom_key = bool(ai_config.api_key)
    return response
