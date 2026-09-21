"""
Adaptador Calendly — SOMENTE LEITURA.

Diferente de Google e Microsoft: o ATENDIT não cria nem cancela nada no
Calendly. O cliente marca pelo link público, o Calendly avisa por webhook e
nós espelhamos em `appointments` com source='calendly'. Por isso este módulo
NÃO implementa create_event/cancel_event — implementar significaria fingir um
controle que não temos.

O que fica guardado, cifrado com CALENDAR_ENCRYPTION_KEY dentro de
calendar_connections.credentials_encrypted:

    token            Personal Access Token da conta
    organization     URI da organização (necessária para registrar o webhook)
    user             URI do usuário
    signing_key      devolvido ao criar a subscription; valida as assinaturas
    scheduling_link  URL pública que a IA informa ao cliente
    webhook_uri      URI da subscription, para poder remover na reconexão

calendar_id na tabela recebe a URI da organização — é o identificador que faz
sentido para este provedor.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import cifrar_dict, decifrar_dict
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Appointment, CalendarConnection, ServiceType

logger = logging.getLogger("atendit.calendly")

PROVIDER = "calendly"
BASE_API = "https://api.calendly.com"

# Tolerância do timestamp da assinatura. Sem ela, uma requisição legítima
# capturada hoje poderia ser reenviada daqui a um mês e ainda validar.
TOLERANCIA_ASSINATURA_SEGUNDOS = 300


class TokenInvalido(RuntimeError):
    """O Personal Access Token foi recusado pelo Calendly."""


class FalhaCalendly(RuntimeError):
    """Erro do lado do Calendly que não é token inválido."""


def _chamar(caminho: str, token: str, metodo: str = "GET", corpo: Optional[dict] = None) -> Dict[str, Any]:
    url = caminho if caminho.startswith("http") else f"{BASE_API}{caminho}"
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        url,
        data=dados,
        method=metodo,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ATENDIT-Core/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            texto = r.read().decode("utf-8")
            return json.loads(texto) if texto else {}
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            # Erro ESPECIFICO, nao generico: o usuario precisa saber que o
            # problema e o token, nao "deu erro".
            raise TokenInvalido(
                "O Calendly recusou este token (HTTP "
                f"{e.code}). Verifique se você colou um Personal Access Token válido "
                "e não expirado, gerado em Integrações → API & Webhooks."
            )
        raise FalhaCalendly(f"Calendly respondeu HTTP {e.code}: {corpo_erro[:300]}")
    except Exception as exc:
        raise FalhaCalendly(f"Não consegui falar com o Calendly: {exc}")


# --------------------------------------------------------------------------
# PARTE A — validação do token
# --------------------------------------------------------------------------
async def validar_token(token: str) -> Dict[str, str]:
    """GET /users/me. Devolve as URIs de usuário e organização."""
    if not (token or "").strip():
        raise TokenInvalido("Informe o Personal Access Token do Calendly.")

    dados = await asyncio.to_thread(_chamar, "/users/me", token.strip())
    recurso = dados.get("resource") or {}
    organizacao = recurso.get("current_organization")
    usuario = recurso.get("uri")

    if not organizacao or not usuario:
        raise FalhaCalendly(
            "O Calendly aceitou o token mas não devolveu a organização. "
            f"Campos recebidos: {sorted(recurso.keys())}"
        )

    logger.info(f"[CALENDLY] Token válido. Usuário: {recurso.get('name')} | org: {organizacao}")
    return {
        "organization": organizacao,
        "user": usuario,
        "name": recurso.get("name") or "",
        "email": recurso.get("email") or "",
        "scheduling_url": recurso.get("scheduling_url") or "",
    }


# --------------------------------------------------------------------------
# PARTE B — subscription do webhook
# --------------------------------------------------------------------------
def _url_webhook(tenant_id: uuid.UUID) -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/calendar/webhook/calendly/{tenant_id}"


async def _remover_subscriptions_antigas(token: str, organizacao: str, url_alvo: str) -> int:
    """
    Apaga subscriptions anteriores apontando para a MESMA url.

    Sem isso, cada reconexão criaria mais uma subscription e o Calendly
    entregaria o mesmo evento duas, três vezes -- e cada entrega criaria uma
    linha em appointments.
    """
    try:
        lista = await asyncio.to_thread(
            _chamar,
            f"/webhook_subscriptions?organization={urllib.parse.quote(organizacao)}&scope=organization&count=100",
            token,
        )
    except Exception as exc:
        logger.warning(f"[CALENDLY] Não consegui listar subscriptions: {exc}")
        return 0

    removidas = 0
    for item in lista.get("collection", []) or []:
        if item.get("callback_url") == url_alvo and item.get("uri"):
            try:
                await asyncio.to_thread(_chamar, item["uri"], token, "DELETE")
                removidas += 1
                logger.info(f"[CALENDLY] Subscription antiga removida: {item['uri']}")
            except Exception as exc:
                logger.warning(f"[CALENDLY] Falha ao remover {item['uri']}: {exc}")
    return removidas


async def registrar_webhook(tenant_id: uuid.UUID, token: str, organizacao: str) -> Dict[str, Any]:
    url = _url_webhook(tenant_id)
    removidas = await _remover_subscriptions_antigas(token, organizacao, url)

    corpo = {
        "url": url,
        "events": ["invitee.created", "invitee.canceled"],
        "organization": organizacao,
        "scope": "organization",
    }
    resposta = await asyncio.to_thread(_chamar, "/webhook_subscriptions", token, "POST", corpo)
    recurso = resposta.get("resource") or {}
    assinatura = recurso.get("signing_key")

    if not assinatura:
        # Sem signing_key nao ha como distinguir webhook legitimo de forjado.
        raise FalhaCalendly(
            "O Calendly criou a assinatura mas não devolveu signing_key; "
            "sem ela não é possível validar os webhooks recebidos."
        )

    logger.info(
        f"[CALENDLY] Webhook registrado para {tenant_id} ({removidas} antiga(s) removida(s))."
    )
    return {"webhook_uri": recurso.get("uri"), "signing_key": assinatura, "removidas": removidas}


async def conectar(tenant_id: uuid.UUID, token: str, scheduling_link: str = "") -> Dict[str, Any]:
    """Valida o token, registra o webhook e grava tudo cifrado."""
    info = await validar_token(token)
    hook = await registrar_webhook(tenant_id, token.strip(), info["organization"])

    segredos = {
        "token": token.strip(),
        "organization": info["organization"],
        "user": info["user"],
        "signing_key": hook["signing_key"],
        "webhook_uri": hook["webhook_uri"],
        # Preferimos o link informado; se vazio, o do proprio perfil Calendly.
        "scheduling_link": (scheduling_link or "").strip() or info.get("scheduling_url", ""),
    }

    async with AsyncSessionLocal() as sessao:
        conexao = (
            await sessao.execute(
                select(CalendarConnection).where(
                    CalendarConnection.tenant_id == tenant_id,
                    CalendarConnection.provider == PROVIDER,
                )
            )
        ).scalar_one_or_none()
        if conexao is None:
            conexao = CalendarConnection(tenant_id=tenant_id, provider=PROVIDER)
            sessao.add(conexao)
        conexao.provider = PROVIDER
        conexao.credentials_encrypted = cifrar_dict(segredos)
        conexao.calendar_id = info["organization"]
        conexao.is_active = True
        await sessao.commit()

    return {
        "conectado": True,
        "provider": PROVIDER,
        "conta": info.get("name"),
        "email": info.get("email"),
        "organization": info["organization"],
        "scheduling_link": segredos["scheduling_link"],
        "subscriptions_removidas": hook["removidas"],
    }


# --------------------------------------------------------------------------
# PARTE C — assinatura e recepção
# --------------------------------------------------------------------------
def verificar_assinatura(signing_key: str, cabecalho: Optional[str], corpo: bytes) -> bool:
    """
    Valida o header Calendly-Webhook-Signature: "t=<unix>,v1=<hmac>".

    O HMAC é sobre "<t>.<corpo_bruto>" — por isso o corpo precisa ser os BYTES
    recebidos, não o JSON re-serializado: qualquer diferença de espaçamento ou
    ordem de chaves mudaria o hash e invalidaria uma requisição legítima.
    """
    if not signing_key or not cabecalho:
        return False

    partes = {}
    for pedaco in cabecalho.split(","):
        if "=" in pedaco:
            k, v = pedaco.strip().split("=", 1)
            partes[k.strip()] = v.strip()

    t, v1 = partes.get("t"), partes.get("v1")
    if not t or not v1:
        return False

    try:
        idade = abs(int(datetime.now(timezone.utc).timestamp()) - int(t))
    except ValueError:
        return False
    if idade > TOLERANCIA_ASSINATURA_SEGUNDOS:
        logger.warning(f"[CALENDLY] Assinatura fora da janela ({idade}s); recusada.")
        return False

    esperado = hmac.new(
        signing_key.encode(), f"{t}.".encode() + corpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, v1)


def _telefone(payload: Dict[str, Any]) -> Optional[str]:
    """
    O telefone é OPCIONAL no Calendly. Pode vir em text_reminder_number ou
    numa questions_and_answers — e pode simplesmente não vir.
    """
    invitee = (payload.get("payload") or {})
    numero = invitee.get("text_reminder_number")
    if numero:
        return "".join(c for c in str(numero) if c.isdigit()) or None

    for qa in invitee.get("questions_and_answers") or []:
        pergunta = (qa.get("question") or "").lower()
        if any(p in pergunta for p in ("telefone", "phone", "whatsapp", "celular")):
            digitos = "".join(c for c in str(qa.get("answer") or "") if c.isdigit())
            if digitos:
                return digitos
    return None


async def processar_evento(tenant_id: uuid.UUID, corpo: Dict[str, Any]) -> Dict[str, Any]:
    evento = (corpo.get("event") or "").strip()
    dados = corpo.get("payload") or {}
    invitee_uri = dados.get("uri") or ""

    if evento not in ("invitee.created", "invitee.canceled"):
        return {"acao": "ignorado", "motivo": f"evento não tratado: '{evento}'"}

    async with AsyncSessionLocal() as sessao:
        servico = (
            await sessao.execute(
                select(ServiceType).where(
                    ServiceType.tenant_id == tenant_id, ServiceType.is_active.is_(True)
                )
            )
        ).scalars().first()
        if servico is None:
            return {"acao": "erro", "motivo": "inquilino sem service_type ativo"}

        existente = (
            await sessao.execute(
                select(Appointment).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.external_event_id == invitee_uri,
                )
            )
        ).scalar_one_or_none()

        if evento == "invitee.canceled":
            if existente is None:
                return {"acao": "ignorado", "motivo": "cancelamento de compromisso desconhecido"}
            existente.status = "cancelled"
            await sessao.commit()
            logger.info(f"[CALENDLY] Compromisso {existente.id} cancelado pelo Calendly.")
            return {"acao": "cancelado", "appointment_id": str(existente.id)}

        # invitee.created — idempotente: o Calendly pode reentregar o mesmo
        # evento, e a URI do invitee e o identificador estavel dele.
        if existente is not None:
            return {"acao": "ignorado", "motivo": "já registrado", "appointment_id": str(existente.id)}

        evento_dados = dados.get("scheduled_event") or {}
        try:
            inicio = datetime.fromisoformat((evento_dados.get("start_time") or "").replace("Z", "+00:00"))
            fim = datetime.fromisoformat((evento_dados.get("end_time") or "").replace("Z", "+00:00"))
        except ValueError:
            return {"acao": "erro", "motivo": "start_time/end_time ausentes ou inválidos"}

        compromisso = Appointment(
            tenant_id=tenant_id,
            service_type_id=servico.id,
            customer_phone=_telefone(corpo) or "",
            customer_name=dados.get("name") or dados.get("email") or "Cliente Calendly",
            start_at=inicio,
            end_at=fim,
            status="confirmed",
            provider=PROVIDER,
            external_event_id=invitee_uri,
            source="calendly",
        )
        sessao.add(compromisso)
        await sessao.commit()
        await sessao.refresh(compromisso)
        logger.info(
            f"[CALENDLY] Compromisso criado {compromisso.id} "
            f"({compromisso.customer_name} em {inicio.isoformat()})."
        )
        return {"acao": "criado", "appointment_id": str(compromisso.id)}


# --------------------------------------------------------------------------
# PARTE D — link para a IA
# --------------------------------------------------------------------------
async def link_de_agendamento(tenant_id: uuid.UUID) -> Optional[str]:
    async with AsyncSessionLocal() as sessao:
        conexao = (
            await sessao.execute(
                select(CalendarConnection).where(
                    CalendarConnection.tenant_id == tenant_id,
                    CalendarConnection.provider == PROVIDER,
                )
            )
        ).scalar_one_or_none()
    if conexao is None or not conexao.credentials_encrypted or not conexao.is_active:
        return None
    try:
        return (decifrar_dict(conexao.credentials_encrypted).get("scheduling_link") or "").strip() or None
    except Exception as exc:
        logger.error(f"[CALENDLY] Não consegui ler o link de {tenant_id}: {exc}")
        return None


async def segredos(tenant_id: uuid.UUID) -> Dict[str, Any]:
    async with AsyncSessionLocal() as sessao:
        conexao = (
            await sessao.execute(
                select(CalendarConnection).where(
                    CalendarConnection.tenant_id == tenant_id,
                    CalendarConnection.provider == PROVIDER,
                )
            )
        ).scalar_one_or_none()
    if conexao is None or not conexao.credentials_encrypted:
        return {}
    return decifrar_dict(conexao.credentials_encrypted)
