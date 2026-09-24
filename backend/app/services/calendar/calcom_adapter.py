"""
Adaptador Cal.com — Modo Headless API v2.

Gerencia a comunicação REST com a infraestrutura do Cal.com:
- Validação de chaves de API (GET /v2/me)
- Criação atômica de reservas (POST /v2/bookings)
- Cancelamento de reservas (DELETE /v2/bookings/{booking_id})
- Consulta de slots livres considerando deslocamento logístico
"""

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.crypto import cifrar_dict, decifrar_dict
from app.core.database import AsyncSessionLocal
from app.models.logistics import LogisticConfig, ServiceOrder
from app.models.scheduling import Appointment, CalendarConnection

logger = logging.getLogger("atendit.calcom")

PROVIDER = "calcom"
BASE_API = "https://api.cal.com/v2"


class TokenInvalido(RuntimeError):
    """A API Key informada foi recusada pelo Cal.com."""


class FalhaCalcom(RuntimeError):
    """Erro de comunicação ou resposta de erro da API Cal.com."""


def _chamar(caminho: str, api_key: str, metodo: str = "GET", corpo: Optional[dict] = None) -> Dict[str, Any]:
    url = caminho if caminho.startswith("http") else f"{BASE_API}{caminho}"
    dados = json.dumps(corpo).encode() if corpo is not None else None
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "cal-api-version": "2024-08-13",
        "User-Agent": "ATENDIT-Core/1.0",
    }
    
    req = urllib.request.Request(url, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            texto = r.read().decode("utf-8")
            return json.loads(texto) if texto else {}
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            raise TokenInvalido(f"API Key do Cal.com recusada (HTTP {e.code}): verifique a chave informada.")
        raise FalhaCalcom(f"Cal.com respondeu HTTP {e.code}: {corpo_erro[:300]}")
    except Exception as exc:
        raise FalhaCalcom(f"Não foi possível conectar à API do Cal.com: {exc}")


async def validar_token(api_key: str) -> Dict[str, Any]:
    """Valida as credenciais do Cal.com consultando o endpoint de perfil."""
    if not (api_key or "").strip():
        raise TokenInvalido("Informe uma API Key válida do Cal.com.")

    dados = await asyncio.to_thread(_chamar, "/me", api_key.strip())
    data = dados.get("data") or dados
    user_id = data.get("id")
    email = data.get("email")

    if not user_id:
        raise FalhaCalcom(f"Resposta inesperada do Cal.com ao validar token: {dados}")

    logger.info(f"[CAL.COM] Credencial validada com sucesso para o usuário {email} (ID: {user_id})")
    return {
        "user_id": user_id,
        "email": email or "",
        "username": data.get("username") or "",
        "name": data.get("name") or "",
    }


async def criar_agendamento(
    tenant_id: uuid.UUID,
    event_type_slug: str,
    inicio_iso: str,
    nome_cliente: str,
    email_cliente: str,
    telefone_cliente: str,
    notas: str = ""
) -> Dict[str, Any]:
    """Cria uma reserva atômica no Cal.com via API v2."""
    async with AsyncSessionLocal() as session:
        cfg = (
            await session.execute(
                select(LogisticConfig).where(LogisticConfig.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

        if not cfg or not cfg.cal_api_key_encrypted:
            raise FalhaCalcom("Inquilino não possui credenciais do Cal.com configuradas.")

        segredos = decifrar_dict(cfg.cal_api_key_encrypted)
        api_key = segredos.get("api_key")

    corpo = {
        "start": inicio_iso,
        "eventSlug": event_type_slug or cfg.cal_event_slug,
        "attendee": {
            "name": nome_cliente,
            "email": email_cliente or f"cliente_{telefone_cliente}@atendit.local",
            "timeZone": "America/Sao_Paulo",
            "phoneNumber": telefone_cliente
        },
        "metadata": {
            "source": "atendit_logistics",
            "notes": notas
        }
    }

    resposta = await asyncio.to_thread(_chamar, "/bookings", api_key, "POST", corpo)
    booking_data = resposta.get("data") or resposta
    logger.info(f"[CAL.COM] Booking criado com sucesso: ID {booking_data.get('id')}")
    return booking_data


async def cancelar_agendamento(tenant_id: uuid.UUID, booking_id: str, motivo: str = "Cancelamento solicitado") -> bool:
    """Cancela uma reserva no Cal.com."""
    async with AsyncSessionLocal() as session:
        cfg = (
            await session.execute(
                select(LogisticConfig).where(LogisticConfig.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

        if not cfg or not cfg.cal_api_key_encrypted:
            return False

        segredos = decifrar_dict(cfg.cal_api_key_encrypted)
        api_key = segredos.get("api_key")

    try:
        corpo = {"cancellationReason": motivo}
        await asyncio.to_thread(_chamar, f"/bookings/{booking_id}/cancel", api_key, "POST", corpo)
        logger.info(f"[CAL.COM] Booking {booking_id} cancelado com sucesso.")
        return True
    except Exception as exc:
        logger.error(f"[CAL.COM] Falha ao cancelar booking {booking_id}: {exc}")
        return False
