import logging
import uuid
from typing import Any, Dict
from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.crypto import cifrar_dict, decifrar_dict
from app.core.database import AsyncSessionLocal
from app.models.logistics import LogisticConfig, ServiceOrder
from app.services.calendar.routes import _exigir_dono

logger = logging.getLogger("atendit.logistics")
router = APIRouter(prefix="/calendar/logistics", tags=["Roteamento Logístico & Cal.com"])

@router.get("/config/{tenant}", include_in_schema=False)
async def obter_config_logistica(tenant: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        cfg = (
            await session.execute(
                select(LogisticConfig).where(LogisticConfig.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

        if not cfg:
            return {
                "tenant_slug": slug,
                "cal_event_slug": "",
                "cal_mode": "headless",
                "base_address": "",
                "radius_km": 35,
                "buffer_traffic": 30,
                "remind_24h": True,
                "remind_2h": True
            }

        return {
            "tenant_slug": slug,
            "cal_event_slug": cfg.cal_event_slug or "",
            "cal_mode": cfg.cal_mode,
            "base_address": cfg.base_address or "",
            "radius_km": cfg.radius_km,
            "buffer_traffic": cfg.buffer_traffic,
            "remind_24h": cfg.remind_24h,
            "remind_2h": cfg.remind_2h
        }

@router.post("/config/{tenant}", include_in_schema=False)
async def salvar_config_logistica(tenant: str, request: Request, payload: Dict[str, Any]):
    tenant_id, slug = await _exigir_dono(request, tenant)
    api_key = (payload.get("cal_api_key") or "").strip()
    cifrado = None
    if api_key and not api_key.startswith("cal_live_••"):
        cifrado = cifrar_dict({"api_key": api_key})

    async with AsyncSessionLocal() as session:
        cfg = (
            await session.execute(
                select(LogisticConfig).where(LogisticConfig.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

        if not cfg:
            cfg = LogisticConfig(tenant_id=tenant_id)
            session.add(cfg)

        if cifrado:
            cfg.cal_api_key_encrypted = cifrado

        cfg.cal_event_slug = (payload.get("cal_event_slug") or "").strip()
        cfg.cal_mode = payload.get("cal_mode", "headless")
        cfg.base_address = (payload.get("base_address") or "").strip()
        cfg.radius_km = int(payload.get("radius_km") or 35)
        cfg.buffer_traffic = int(payload.get("buffer_traffic") or 30)
        cfg.remind_24h = bool(payload.get("remind_24h", True))
        cfg.remind_2h = bool(payload.get("remind_2h", True))

        await session.commit()
        logger.info(f"[LOGISTICA] Parâmetros guardados para o inquilino {slug} ({tenant_id})")

    return {"status": "success", "tenant_slug": slug}
