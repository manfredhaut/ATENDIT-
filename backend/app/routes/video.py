import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.video import VideoRoom
from app.models.scheduling import ServiceType
from app.models.tenant import Tenant

logger = logging.getLogger("atendit.video")

router = APIRouter(tags=["Videoconferência WebRTC & Tour Guiado"])


class WebRTCConnectionManager:
    def __init__(self):
        self.active_rooms: Dict[str, List[WebSocket]] = {}

    async def connect(self, room_token: str, websocket: WebSocket):
        await websocket.accept()
        if room_token not in self.active_rooms:
            self.active_rooms[room_token] = []
        self.active_rooms[room_token].append(websocket)
        logger.info(f"[WEBRTC WS] Conexão estabelecida na sala '{room_token}'. Participantes ativos: {len(self.active_rooms[room_token])}")

    def disconnect(self, room_token: str, websocket: WebSocket):
        if room_token in self.active_rooms:
            if websocket in self.active_rooms[room_token]:
                self.active_rooms[room_token].remove(websocket)
            if not self.active_rooms[room_token]:
                del self.active_rooms[room_token]
        logger.info(f"[WEBRTC WS] Conexão encerrada na sala '{room_token}'.")

    async def broadcast_except(self, room_token: str, message: dict, sender_socket: WebSocket):
        if room_token in self.active_rooms:
            msg_text = json.dumps(message)
            for connection in self.active_rooms[room_token]:
                if connection != sender_socket:
                    try:
                        await connection.send_text(msg_text)
                    except Exception as e:
                        logger.error(f"[WEBRTC WS BROADCAST ERROR] {e}")


manager = WebRTCConnectionManager()


@router.get("/meet/{room_token}", response_class=HTMLResponse, include_in_schema=False)
async def carregar_sala_video(room_token: str):
    """
    Serve a interface pública da videoconferência WebRTC e Tour Guiado via token seguro efêmero.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(VideoRoom).where(VideoRoom.room_token == room_token)
        res = await session.execute(stmt)
        sala = res.scalars().first()

        if not sala:
            return HTMLResponse("<h2 style='font-family:sans-serif;text-align:center;margin-top:50px;'>Sala não encontrada ou token inválido.</h2>", status_code=404)

        if sala.expires_at < datetime.now(timezone.utc):
            return HTMLResponse("<h2 style='font-family:sans-serif;text-align:center;margin-top:50px;'>Esta sala expirou. Solicite um novo link pelo WhatsApp.</h2>", status_code=410)

    html_path = Path(__file__).resolve().parent.parent / "frontend" / "meet.html"
    if not html_path.is_file():
        return HTMLResponse("<h2 style='font-family:sans-serif;text-align:center;margin-top:50px;'>Interface em carregamento. Tente em instantes.</h2>", status_code=503)

    with open(html_path, "r", encoding="utf-8") as f:
        html_conteudo = f.read()

    return HTMLResponse(content=html_conteudo)


@router.get("/v1/video/rooms/{room_token}")
async def obter_detalhes_sala(room_token: str):
    """
    Retorna os dados da sala, contexto das tags do JEV e catálogo de produtos/serviços para o Tour Guiado.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(VideoRoom).where(VideoRoom.room_token == room_token)
        res = await session.execute(stmt)
        sala = res.scalars().first()

        if not sala:
            raise HTTPException(status_code=404, detail="Sala não encontrada")

        stmt_t = select(Tenant).where(Tenant.id == sala.tenant_id)
        res_t = await session.execute(stmt_t)
        tenant = res_t.scalars().first()

        stmt_s = select(ServiceType).where(
            ServiceType.tenant_id == sala.tenant_id,
            ServiceType.is_active == True
        )
        res_s = await session.execute(stmt_s)
        servicos = res_s.scalars().all()

        catalogo = [
            {
                "id": str(s.id),
                "sku": f"SRV-{s.id.hex[:6].upper()}",
                "name": s.name,
                "duration_minutes": s.duration_minutes,
                "description": s.description or "Atendimento técnico e consultoria especializada.",
                "price": "R$ 180,00",
                "schema_type": "Service"
            }
            for s in servicos
        ]

        if not catalogo:
            catalogo = [
                {
                    "id": "item_visita",
                    "sku": "SRV-MANUT-01",
                    "name": "Visita Técnica & Diagnóstico",
                    "duration_minutes": 60,
                    "description": "Inspeção completa de hardware, conectividade e certificação operacional.",
                    "price": "R$ 180,00",
                    "schema_type": "Service"
                },
                {
                    "id": "item_placa",
                    "sku": "PRD-INVER-02",
                    "name": "Placa Inversora Controladora",
                    "duration_minutes": 0,
                    "description": "Módulo original de potência com proteção contra transientes e alta estabilidade.",
                    "price": "R$ 490,00",
                    "schema_type": "Product"
                }
            ]

        return {
            "room_token": sala.room_token,
            "customer_name": sala.customer_name,
            "status": sala.status,
            "tags_context": sala.tags_context,
            "tenant_name": tenant.name if tenant else "ATENDIT",
            "catalogo": catalogo
        }


@router.websocket("/ws/video/{room_token}")
async def webrtc_signaling_websocket(websocket: WebSocket, room_token: str):
    """
    Sinalização WebRTC P2P e sincronização do Tour Guiado transmitida nativamente pelo Traefik v3.1.
    """
    await manager.connect(room_token, websocket)
    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                msg = json.loads(data_text)
            except Exception:
                continue

            await manager.broadcast_except(room_token, msg, websocket)

    except WebSocketDisconnect:
        manager.disconnect(room_token, websocket)
    except Exception as exc:
        logger.error(f"[WEBRTC WS EXCEPTION] {exc}")
        manager.disconnect(room_token, websocket)


from pydantic import BaseModel

class WebRTCConfigSchema(BaseModel):
    provider: Optional[str] = "livekit"
    api_key: Optional[str] = None
    domain: Optional[str] = None
    guided_tour_enabled: Optional[bool] = True
    jev_threshold: Optional[float] = 0.75

@router.get("/v1/video/config/{slug_or_id}")
async def obter_config_webrtc(slug_or_id: str):
    async with AsyncSessionLocal() as session:
        # Busca por slug ou por ID
        try:
            uid = uuid.UUID(slug_or_id)
            stmt = select(Tenant).where(Tenant.id == uid)
        except ValueError:
            stmt = select(Tenant).where(Tenant.slug == slug_or_id)

        res = await session.execute(stmt)
        tenant = res.scalars().first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant não encontrado")

        meta = tenant.meta_data or {}
        webrtc_cfg = meta.get("webrtc", {})
        return {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "provider": webrtc_cfg.get("provider", "livekit"),
            "domain": webrtc_cfg.get("domain", ""),
            "guided_tour_enabled": webrtc_cfg.get("guided_tour_enabled", True),
            "jev_threshold": webrtc_cfg.get("jev_threshold", 0.75),
            "has_key": bool(webrtc_cfg.get("api_key"))
        }

@router.post("/v1/video/config/{slug_or_id}")
async def salvar_config_webrtc(slug_or_id: str, payload: WebRTCConfigSchema):
    async with AsyncSessionLocal() as session:
        try:
            uid = uuid.UUID(slug_or_id)
            stmt = select(Tenant).where(Tenant.id == uid)
        except ValueError:
            stmt = select(Tenant).where(Tenant.slug == slug_or_id)

        res = await session.execute(stmt)
        tenant = res.scalars().first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant não encontrado")

        # Atualiza o dicionário meta_data
        meta = dict(tenant.meta_data or {})
        webrtc_cfg = dict(meta.get("webrtc", {}))

        webrtc_cfg["provider"] = payload.provider or "livekit"
        if payload.api_key and not payload.api_key.startswith("••"):
            webrtc_cfg["api_key"] = payload.api_key
        webrtc_cfg["domain"] = payload.domain or ""
        webrtc_cfg["guided_tour_enabled"] = payload.guided_tour_enabled if payload.guided_tour_enabled is not None else True
        webrtc_cfg["jev_threshold"] = payload.jev_threshold or 0.75

        meta["webrtc"] = webrtc_cfg
        tenant.meta_data = meta

        await session.commit()
        logger.info(f"[WEBRTC CONFIG] Configurações WebRTC/JEV salvas com sucesso para tenant '{tenant.slug}'.")
        return {"sucesso": True, "mensagem": "Configurações salvas com sucesso."}
