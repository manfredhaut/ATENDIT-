"""Roteador de Governanca e Telemetria Presenthia Master Console."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.feature_flags import flag_on
from app.models.feature_flag import FeatureFlag
from app.models.tenant import Tenant

logger = logging.getLogger("presenthia.console")

router = APIRouter(
    prefix="/api/v1/presenthia",
    tags=["Presenthia Master Console"],
    dependencies=[Depends(flag_on("presenthia_core"))],
)

class TenantSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    admin_email: str
    is_active: bool
    evolution_instance: Optional[str] = None
    whatsapp_number_e164: Optional[str] = None
    trial_days: int
    trial_ends_at: datetime
    created_at: datetime

class FlagSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    description: Optional[str] = None
    enabled: bool
    scope: str
    tenant_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

class StatsResponseSchema(BaseModel):
    total_tenants: int
    active_tenants: int
    inactive_tenants: int
    instances_connected: int
    whatsapp_active: int
    total_flags: int
    active_flags: int
    environment: str
    platform_status: str
    timestamp: datetime

@router.get("/stats", response_model=StatsResponseSchema)
async def get_console_stats(db: AsyncSession = Depends(get_db)):
    try:
        total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar() or 0
        active_tenants = (await db.execute(select(func.count(Tenant.id)).where(Tenant.is_active.is_(True)))).scalar() or 0
        inactive_tenants = total_tenants - active_tenants
        instances_connected = (await db.execute(select(func.count(Tenant.id)).where(Tenant.evolution_instance.is_not(None)))).scalar() or 0
        whatsapp_active = (await db.execute(select(func.count(Tenant.id)).where(Tenant.whatsapp_jid.is_not(None)))).scalar() or 0
        total_flags = (await db.execute(select(func.count(FeatureFlag.id)))).scalar() or 0
        active_flags = (await db.execute(select(func.count(FeatureFlag.id)).where(FeatureFlag.enabled.is_(True)))).scalar() or 0
        return StatsResponseSchema(
            total_tenants=total_tenants,
            active_tenants=active_tenants,
            inactive_tenants=inactive_tenants,
            instances_connected=instances_connected,
            whatsapp_active=whatsapp_active,
            total_flags=total_flags,
            active_flags=active_flags,
            environment="production",
            platform_status="operational",
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error(f"[PRESENTHIA STATS ERROR] Falha: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

@router.get("/tenants", response_model=List[TenantSummarySchema])
async def list_tenants(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    try:
        stmt = select(Tenant).order_by(Tenant.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        tenants = result.scalars().all()
        return [TenantSummarySchema.model_validate(t) for t in tenants]
    except Exception as exc:
        logger.error(f"[PRESENTHIA TENANTS ERROR] Falha: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao listar inquilinos.")

@router.post("/tenants/{tenant_id}/toggle-active")
async def toggle_tenant_active(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inquilino nao encontrado.")
    tenant.is_active = not tenant.is_active
    tenant.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "tenant_id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "is_active": tenant.is_active}

@router.get("/flags", response_model=List[FlagSummarySchema])
async def list_feature_flags(db: AsyncSession = Depends(get_db)):
    try:
        stmt = select(FeatureFlag).order_by(FeatureFlag.key.asc())
        result = await db.execute(stmt)
        flags = result.scalars().all()
        return [FlagSummarySchema.model_validate(f) for f in flags]
    except Exception as exc:
        logger.error(f"[PRESENTHIA FLAGS ERROR] Falha: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao listar flags.")

@router.post("/flags/{flag_key}/toggle")
async def toggle_feature_flag(flag_key: str, db: AsyncSession = Depends(get_db)):
    stmt = select(FeatureFlag).where(FeatureFlag.key == flag_key.strip())
    flag = (await db.execute(stmt)).scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flag nao encontrada.")
    flag.enabled = not flag.enabled
    flag.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "key": flag.key, "enabled": flag.enabled, "updated_at": flag.updated_at.isoformat()}
