import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import exigir_token_interno
from app.models.tenant import AIConfig, Tenant

logger = logging.getLogger("atendit.tenants")

router = APIRouter(prefix="/tenants", tags=["Tenants Engine"])


# --- SCHEMAS PYDANTIC ---
class TenantCreateSchema(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nome da empresa ou cliente")
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9_-]+$", description="Identificador único para URL/Tenant")
    admin_email: EmailStr = Field(..., description="E-mail do administrador do tenant")
    trial_days: int = Field(default=7, ge=1, le=90, description="Duração do período de testes em dias")
    meta_data: Optional[dict] = Field(default_factory=dict, description="Metadados adicionais do tenant")


class TenantResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    admin_email: str
    trial_days: int
    trial_ends_at: datetime
    is_active: bool
    meta_data: dict
    created_at: datetime
    updated_at: datetime


# --- ROTAS ASSÍNCRONAS COM PERSISTÊNCIA NO POSTGRESQL ---
@router.post(
    "/",
    response_model=TenantResponseSchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exigir_token_interno)],
)
async def create_tenant(
    payload: TenantCreateSchema,
    db: AsyncSession = Depends(get_db)
):
    """
    Cadastra e provisiona um novo Tenant no banco de dados relacional.
    Cria automaticamente a configuração de IA default para a nova instância.
    """
    logger.info(f"[TENANT ENGINE] Solicitação de Onboarding recebida para slug: '{payload.slug}'")

    # Verifica se já existe um tenant com o mesmo slug
    stmt = select(Tenant).where(Tenant.slug == payload.slug)
    result = await db.execute(stmt)
    existing_tenant = result.scalar_one_or_none()

    if existing_tenant:
        logger.warning(f"[TENANT ENGINE] Conflito: Slug '{payload.slug}' já está em uso.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"O identificador de tenant '{payload.slug}' já existe no sistema."
        )

    # Cálculo da vigência de trial
    trial_limit = datetime.now(timezone.utc) + timedelta(days=payload.trial_days)

    new_tenant = Tenant(
        name=payload.name,
        slug=payload.slug,
        admin_email=payload.admin_email,
        trial_days=payload.trial_days,
        trial_ends_at=trial_limit,
        is_active=True,
        meta_data=payload.meta_data or {}
    )

    # Inicializa a configuração de IA vinculada (1:1)
    default_ai_config = AIConfig(
        provider="gemini",
        agent_name="ManiBot",
        model="gemini-1.5-flash",
        temperature=0.7,
        is_active=True,
        meta_data={}
    )
    new_tenant.ai_config = default_ai_config

    db.add(new_tenant)
    await db.flush()
    await db.refresh(new_tenant)

    logger.info(f"[TENANT ENGINE] Tenant provisionado com sucesso: ID={new_tenant.id}, Slug='{new_tenant.slug}'")
    return new_tenant


@router.get("/", response_model=List[TenantResponseSchema])
async def list_tenants(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos os tenants cadastrados com paginação."""
    stmt = select(Tenant).order_by(Tenant.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    tenants = result.scalars().all()
    return tenants


@router.get("/{tenant_id}", response_model=TenantResponseSchema)
async def get_tenant_by_id(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Consulta detalhes de um tenant específico por UUID."""
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if not tenant:
        logger.warning(f"[TENANT ENGINE] Tenant não localizado: ID={tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant não localizado."
        )

    return tenant
