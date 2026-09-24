import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, status, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.meta import TenantMetaConfig
from app.core import autorizacao as _autz

router = APIRouter(prefix="/v1/meta", tags=["Meta Cloud API"])

def _validar_uuid(identificador: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(identificador).strip())
    except Exception:
        raise HTTPException(status_code=400, detail="UUID invalido")

class MetaConfigPayload(BaseModel):
    app_id: Optional[str] = None
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    access_token: Optional[str] = None
    verify_token: str
    business_name: Optional[str] = None
    is_active: bool = True

@router.get("/webhook/{tenant_id}")
async def meta_webhook_verification(
    tenant_id: str,
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    t_uuid = _validar_uuid(tenant_id)
    async with AsyncSessionLocal() as session:
        stmt = select(TenantMetaConfig).where(TenantMetaConfig.tenant_id == t_uuid)
        cfg = (await session.execute(stmt)).scalar_one_or_none()
        if not cfg or not cfg.verify_token:
            raise HTTPException(status_code=403, detail="Tenant sem configuracao Meta ativa")
        if hub_mode == "subscribe" and hub_verify_token == cfg.verify_token:
            return Response(content=hub_challenge or "", media_type="text/plain")
        raise HTTPException(status_code=403, detail="Falha de verificacao do webhook")

@router.post("/webhook/{tenant_id}")
async def meta_webhook_receive(tenant_id: str, request: Request):
    t_uuid = _validar_uuid(tenant_id)
    payload = await request.json()
    return {"status": "received", "tenant_id": str(t_uuid)}

@router.get("/config/{tenant_id}")
async def get_meta_config(tenant_id: str, request: Request):
    t_uuid = _validar_uuid(tenant_id)
    await _autz.exigir_acesso_ao_tenant(request, tenant_id=t_uuid)
    async with AsyncSessionLocal() as session:
        stmt = select(TenantMetaConfig).where(TenantMetaConfig.tenant_id == t_uuid)
        cfg = (await session.execute(stmt)).scalar_one_or_none()
        if not cfg:
            return {"configured": False, "verify_token": str(uuid.uuid4())}
        return {
            "configured": True,
            "app_id": cfg.app_id,
            "waba_id": cfg.waba_id,
            "phone_number_id": cfg.phone_number_id,
            "business_name": cfg.business_name,
            "verify_token": cfg.verify_token,
            "is_active": cfg.is_active,
            "has_token": bool(cfg.access_token)
        }

@router.post("/config/{tenant_id}")
async def save_meta_config(tenant_id: str, payload: MetaConfigPayload, request: Request):
    t_uuid = _validar_uuid(tenant_id)
    await _autz.exigir_acesso_ao_tenant(request, tenant_id=t_uuid)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(TenantMetaConfig).where(TenantMetaConfig.tenant_id == t_uuid)
            cfg = (await session.execute(stmt)).scalar_one_or_none()
            if not cfg:
                cfg = TenantMetaConfig(tenant_id=t_uuid, verify_token=payload.verify_token)
                session.add(cfg)
            cfg.app_id = payload.app_id
            cfg.waba_id = payload.waba_id
            cfg.phone_number_id = payload.phone_number_id
            if payload.access_token:
                cfg.access_token = payload.access_token
            cfg.verify_token = payload.verify_token
            cfg.business_name = payload.business_name
            cfg.is_active = payload.is_active
    return {"status": "success", "tenant_id": str(t_uuid)}
