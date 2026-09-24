from collections import deque
import logging

class InMemoryLogBuffer(logging.Handler):
    def __init__(self, capacity=100):
        super().__init__()
        self.buffer = deque(maxlen=capacity)
    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

log_buffer_handler = InMemoryLogBuffer(capacity=100)
log_buffer_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.getLogger().addHandler(log_buffer_handler)

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
from app.models.tenant import Tenant, AIConfig
from app.models.rag import RAGDocument

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


class CopilotSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    agent_name: str
    provider: str
    model: str
    temperature: float
    is_active: bool
    has_custom_api_key: bool
    rag_docs_count: int
    system_instruction_preview: Optional[str] = None
    updated_at: datetime

class SubscriptionSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    admin_email: str
    plan_name: str
    is_active: bool
    trial_days: int
    trial_ends_at: datetime
    days_remaining: int
    status_billing: str
    whatsapp_status: str

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


@router.get("/copilots", response_model=List[CopilotSummarySchema])
async def list_copilots(db: AsyncSession = Depends(get_db)):
    """Retorna o panorama operacional de todos os copilotos de IA e bases RAG."""
    try:
        # Carregar inquilinos com sua configuracao de IA
        stmt = select(Tenant).order_by(Tenant.name.asc())
        result = await db.execute(stmt)
        tenants = result.scalars().all()

        # Contagem de documentos RAG por tenant
        rag_stmt = select(RAGDocument.tenant_id, func.count(RAGDocument.id)).group_by(RAGDocument.tenant_id)
        rag_counts = dict((await db.execute(rag_stmt)).all())

        copilots_list = []
        for t in tenants:
            ai = t.ai_config
            docs_count = rag_counts.get(t.id, 0)
            
            if ai:
                copilots_list.append(CopilotSummarySchema(
                    tenant_id=t.id,
                    tenant_name=t.name,
                    tenant_slug=t.slug,
                    agent_name=ai.agent_name or "Não Definido",
                    provider=ai.provider,
                    model=ai.model,
                    temperature=ai.temperature,
                    is_active=ai.is_active,
                    has_custom_api_key=bool(ai.api_key),
                    rag_docs_count=docs_count,
                    system_instruction_preview=(ai.system_instruction[:80] + "...") if ai.system_instruction else "Padrão do Sistema",
                    updated_at=ai.updated_at
                ))
            else:
                copilots_list.append(CopilotSummarySchema(
                    tenant_id=t.id,
                    tenant_name=t.name,
                    tenant_slug=t.slug,
                    agent_name="Padrão (Sistema)",
                    provider="gemini",
                    model="gemini-1.5-flash",
                    temperature=0.7,
                    is_active=False,
                    has_custom_api_key=False,
                    rag_docs_count=docs_count,
                    system_instruction_preview="Não configurado",
                    updated_at=t.updated_at
                ))

        return copilots_list
    except Exception as exc:
        logger.error(f"[PRESENTHIA COPILOTS ERROR] Falha: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao listar copilotos de IA.")

@router.post("/copilots/{tenant_id}/toggle-active")
async def toggle_copilot_active(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Alterna o status de ativacao do copiloto de IA de um inquilino."""
    stmt = select(AIConfig).where(AIConfig.tenant_id == tenant_id)
    ai = (await db.execute(stmt)).scalar_one_or_none()
    if not ai:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configuracao de IA nao encontrada para este inquilino.")
    ai.is_active = not ai.is_active
    ai.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "tenant_id": str(tenant_id), "agent_name": ai.agent_name, "is_active": ai.is_active}

@router.get("/subscriptions", response_model=List[SubscriptionSummarySchema])
async def list_subscriptions(db: AsyncSession = Depends(get_db)):
    """Retorna o panorama de assinaturas, vigência de trial e faturamento."""
    try:
        stmt = select(Tenant).order_by(Tenant.created_at.desc())
        result = await db.execute(stmt)
        tenants = result.scalars().all()

        now = datetime.now(timezone.utc)
        subs = []
        for t in tenants:
            meta = t.meta_data or {}
            plan_name = meta.get("plan", "Trial Standard")
            
            # Calculo de dias restantes
            delta_days = (t.trial_ends_at - now).days if t.trial_ends_at else 0
            if delta_days > 0:
                status_b = f"Vigente ({delta_days}d restantes)"
            elif delta_days == 0:
                status_b = "Expira Hoje"
            else:
                status_b = f"Expirado há {abs(delta_days)}d"

            whatsapp_st = "Conectado" if t.whatsapp_jid or t.evolution_instance else "Desconectado"

            subs.append(SubscriptionSummarySchema(
                tenant_id=t.id,
                tenant_name=t.name,
                tenant_slug=t.slug,
                admin_email=t.admin_email,
                plan_name=plan_name,
                is_active=t.is_active,
                trial_days=t.trial_days,
                trial_ends_at=t.trial_ends_at,
                days_remaining=delta_days,
                status_billing=status_b,
                whatsapp_status=whatsapp_st
            ))
        return subs
    except Exception as exc:
        logger.error(f"[PRESENTHIA SUBS ERROR] Falha: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao listar assinaturas.")

@router.get("/pulso")
async def get_operation_pulse():
    """Retorna telemetria do Celery, Redis e Sistema Operacional via /proc nativo do Linux."""
    import os
    from app.core.celery_app import celery_app

    # Coleta de Memória nativa via /proc/meminfo
    ram_percent = 0.0
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].split()[0])
        total = meminfo.get("MemTotal", 1)
        avail = meminfo.get("MemAvailable", total)
        ram_percent = round(((total - avail) / total) * 100, 1)
    except Exception:
        pass

    # Coleta de Carga da CPU (loadavg 1 min)
    cpu_percent = 0.0
    try:
        load1, _, _ = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        cpu_percent = round(min(100.0, (load1 / cpu_count) * 100), 1)
    except Exception:
        pass

    pulse_data = {
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "workers_online": 0,
        "tasks_active": 0,
        "tasks_registered": 0,
        "broker_status": "Desconectado"
    }

    try:
        insp = celery_app.control.inspect(timeout=1.5)
        stats = insp.stats()
        if stats:
            pulse_data["broker_status"] = "Conectado"
            pulse_data["workers_online"] = len(stats.keys())
            active = insp.active()
            if active:
                pulse_data["tasks_active"] = sum(len(tasks) for tasks in active.values())
            registered = insp.registered()
            if registered:
                pulse_data["tasks_registered"] = sum(len(tasks) for tasks in registered.values())
    except Exception as e:
        logger.warning(f"[PULSO] Falha na telemetria Celery: {e}")

    return pulse_data


@router.get("/auditoria")
async def get_audit_logs():
    """Retorna os eventos mais recentes do buffer de logs da aplicação."""
    logs = list(log_buffer_handler.buffer)
    if not logs:
        logs = [f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} [INFO] presenthia.audit: Buffer ativo. Aguardando novos eventos..."]
    return {"api_logs": logs[-30:]}
