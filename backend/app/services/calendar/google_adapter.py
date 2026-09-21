"""
Adaptador do Google Calendar — OAuth2, disponibilidade e eventos.

Este módulo NÃO define rotas. A camada de rotas é genérica por provedor e
vive em app/services/calendar/routes.py; aqui ficam só as operações
específicas do Google. Qualquer provedor novo (Microsoft, CalDAV, Calendly)
implementa esta MESMA interface e é plugado em ADAPTADORES:

    iniciar_consentimento(tenant_id)              -> url
    conferir_state(state)                         -> uuid.UUID
    concluir_consentimento(tenant_id, state, code)-> dict
    email_da_conta(tenant_id)                     -> str
    list_free_slots / create_event / cancel_event

As chamadas do googleapiclient são SÍNCRONAS e bloqueiam. Como a API é
async, todas passam por asyncio.to_thread: sem isso, uma consulta lenta ao
Google travaria o event loop e derrubaria o atendimento de todos os
inquilinos, não só o da chamada.
"""

import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import cifrar_dict, decifrar_dict
from app.core.database import AsyncSessionLocal
from app.models.scheduling import CalendarConnection

logger = logging.getLogger("atendit.google_calendar")

PROVIDER = "google"

ESCOPOS = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

# 30 min: a tentativa de 2026-08-31 levou 9min54s entre /start e callback,
# raspando num teto de 10 min. O verifier sozinho nao autoriza nada sem o
# code do Google, entao a folga custa pouco e evita queimar autorizacao.
TTL_PKCE_SEGUNDOS = 1800


class ConsentimentoExpirado(RuntimeError):
    """O code_verifier do PKCE não está mais no Redis — fluxo precisa recomeçar."""


def _redirect_uri() -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/calendar/oauth/{PROVIDER}/callback"


def _config_cliente() -> Dict[str, Any]:
    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth do Google não configurado (GOOGLE_OAUTH_CLIENT_ID/SECRET ausentes).",
        )
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [_redirect_uri()],
        }
    }


# --------------------------------------------------------------------------
# state assinado
# --------------------------------------------------------------------------
def _assinar_state(tenant_id: str) -> str:
    """
    Sem assinatura, qualquer pessoa chamaria o callback com o tenant_id de
    outro inquilino e amarraria a PRÓPRIA agenda Google à conta alheia — ou
    sobrescreveria a conexão existente de um cliente.
    """
    chave = (settings.INTERNAL_API_TOKEN or "").encode()
    assinatura = hmac.new(chave, tenant_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{tenant_id}.{assinatura}"


def conferir_state(state: str) -> uuid.UUID:
    try:
        tenant_id, _ = state.rsplit(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Parâmetro 'state' malformado.")
    if not hmac.compare_digest(_assinar_state(tenant_id), state):
        logger.warning("[GCAL OAUTH] state com assinatura inválida — callback recusado.")
        raise HTTPException(status_code=400, detail="Assinatura do 'state' inválida.")
    try:
        return uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="'state' não contém um UUID válido.")


# --------------------------------------------------------------------------
# PKCE: o code_verifier precisa SOBREVIVER entre /start e /callback
# --------------------------------------------------------------------------
# O google-auth-oauthlib gera um code_verifier dentro do Flow ao montar a URL
# e envia só o code_challenge ao Google. No callback criamos um Flow NOVO, sem
# esse verifier -> "(invalid_grant) Missing code verifier". O Redis é o que
# liga as duas metades do fluxo.
def _chave_pkce(state: str) -> str:
    return f"oauth:pkce:{state}"


async def _cliente_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def _fechar(cliente) -> None:
    try:
        await cliente.aclose()
    except AttributeError:
        await cliente.close()


async def _guardar_verifier(state: str, verifier: str) -> None:
    cliente = await _cliente_redis()
    try:
        await cliente.setex(_chave_pkce(state), TTL_PKCE_SEGUNDOS, verifier)
        logger.info(f"[GCAL OAUTH] code_verifier guardado ({TTL_PKCE_SEGUNDOS}s de validade).")
    finally:
        await _fechar(cliente)


async def _recuperar_verifier(state: str) -> Optional[str]:
    cliente = await _cliente_redis()
    try:
        return await cliente.get(_chave_pkce(state))
    finally:
        await _fechar(cliente)


async def _descartar_verifier(state: str) -> None:
    cliente = await _cliente_redis()
    try:
        await cliente.delete(_chave_pkce(state))
    finally:
        await _fechar(cliente)


# --------------------------------------------------------------------------
# Fluxo de consentimento
# --------------------------------------------------------------------------
async def iniciar_consentimento(tenant_id: uuid.UUID) -> str:
    fluxo = Flow.from_client_config(_config_cliente(), scopes=ESCOPOS, redirect_uri=_redirect_uri())
    state = _assinar_state(str(tenant_id))
    url, _ = fluxo.authorization_url(
        # offline + consent: sem os dois, o Google devolve refresh_token apenas
        # na PRIMEIRA autorização da conta. Numa reconexão viria só o access
        # token, que expira em 1h, e a agenda pararia sozinha depois disso.
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    # Antes do redirect: depois dele o processo não tem mais o objeto Flow.
    await _guardar_verifier(state, fluxo.code_verifier)
    return url


async def concluir_consentimento(tenant_id: uuid.UUID, state: str, code: str) -> Dict[str, Any]:
    verifier = await _recuperar_verifier(state)
    if not verifier:
        raise ConsentimentoExpirado("code_verifier ausente ou expirado.")

    fluxo = Flow.from_client_config(_config_cliente(), scopes=ESCOPOS, redirect_uri=_redirect_uri())
    fluxo.code_verifier = verifier

    def _trocar():
        fluxo.fetch_token(code=code)
        return fluxo.credentials

    cred = await asyncio.to_thread(_trocar)

    dados = {
        "token": cred.token,
        "refresh_token": cred.refresh_token,
        "token_uri": cred.token_uri,
        "client_id": cred.client_id,
        "client_secret": cred.client_secret,
        "scopes": list(cred.scopes or []),
        "expiry": cred.expiry.isoformat() if cred.expiry else None,
    }
    cifrado = cifrar_dict(dados)

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
        conexao.credentials_encrypted = cifrado
        conexao.calendar_id = conexao.calendar_id or "primary"
        conexao.is_active = True
        await sessao.commit()

    # O verifier é de uso único; deixá-lo vivo só amplia a janela de reuso.
    await _descartar_verifier(state)

    logger.info(
        f"[GCAL OAUTH SUCESSO] Tenant {tenant_id} conectado. "
        f"refresh_token presente: {bool(cred.refresh_token)}. Cifrado: {len(cifrado)} bytes."
    )
    return {"tem_refresh_token": bool(cred.refresh_token), "bytes_cifrados": len(cifrado)}


# --------------------------------------------------------------------------
# Cliente autenticado
# --------------------------------------------------------------------------
async def _carregar_conexao(tenant_id: uuid.UUID) -> CalendarConnection:
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
        raise RuntimeError(f"Inquilino {tenant_id} não tem calendário Google conectado.")
    if not conexao.is_active:
        raise RuntimeError(f"Conexão de calendário do inquilino {tenant_id} está inativa.")
    return conexao


def _servico(dados: Dict[str, Any]):
    cred = Credentials(
        token=dados.get("token"),
        refresh_token=dados.get("refresh_token"),
        token_uri=dados.get("token_uri", TOKEN_URI),
        client_id=dados.get("client_id"),
        client_secret=dados.get("client_secret"),
        scopes=dados.get("scopes") or ESCOPOS,
    )
    # cache_discovery=False evita escrita de cache em disco dentro do container.
    return build("calendar", "v3", credentials=cred, cache_discovery=False)


async def _servico_do_tenant(tenant_id: uuid.UUID):
    conexao = await _carregar_conexao(tenant_id)
    dados = decifrar_dict(conexao.credentials_encrypted)
    servico = await asyncio.to_thread(_servico, dados)
    return servico, (conexao.calendar_id or "primary")


async def email_da_conta(tenant_id: uuid.UUID) -> Optional[str]:
    """
    E-mail da conta conectada. No Google Calendar, o id do calendário
    'primary' É o endereço da conta — não precisa do escopo de perfil.
    """
    servico, cal = await _servico_do_tenant(tenant_id)

    def _consultar():
        return servico.calendarList().get(calendarId=cal).execute()

    info = await asyncio.to_thread(_consultar)
    return info.get("id")


# --------------------------------------------------------------------------
# Operações de calendário
# --------------------------------------------------------------------------
async def list_free_slots(
    tenant_id: uuid.UUID,
    date_range: Tuple[datetime, datetime],
    duration_minutes: int,
    calendar_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Janelas livres da agenda, fatiadas em blocos de duration_minutes.

    Usa freebusy.query, que devolve os intervalos OCUPADOS; o livre é o
    complemento. Listar eventos traria também os marcados como 'disponível'
    e os recusados, que não bloqueiam agenda — o freebusy já aplica isso.

    NÃO cruza com business_hours: isso é do scheduling_service, que conhece
    o horário comercial do inquilino.
    """
    inicio, fim = date_range
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=timezone.utc)
    if duration_minutes <= 0:
        raise ValueError("duration_minutes deve ser maior que zero.")

    servico, cal_padrao = await _servico_do_tenant(tenant_id)
    cal = calendar_id or cal_padrao

    def _consultar():
        corpo = {"timeMin": inicio.isoformat(), "timeMax": fim.isoformat(), "items": [{"id": cal}]}
        return servico.freebusy().query(body=corpo).execute()

    resposta = await asyncio.to_thread(_consultar)
    calendario = (resposta.get("calendars") or {}).get(cal, {})
    if calendario.get("errors"):
        raise RuntimeError(f"Google recusou a consulta ao calendário '{cal}': {calendario['errors']}")

    ocupados = [
        (
            datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
        )
        for b in calendario.get("busy", [])
    ]
    ocupados.sort()

    # Funde blocos sobrepostos: sem isso o complemento geraria janelas
    # negativas onde dois compromissos se cruzam.
    fundidos: List[Tuple[datetime, datetime]] = []
    for ini_o, fim_o in ocupados:
        if fundidos and ini_o <= fundidos[-1][1]:
            fundidos[-1] = (fundidos[-1][0], max(fundidos[-1][1], fim_o))
        else:
            fundidos.append((ini_o, fim_o))

    livres: List[Tuple[datetime, datetime]] = []
    cursor = inicio
    for ini_o, fim_o in fundidos:
        if ini_o > cursor:
            livres.append((cursor, min(ini_o, fim)))
        cursor = max(cursor, fim_o)
        if cursor >= fim:
            break
    if cursor < fim:
        livres.append((cursor, fim))

    duracao = timedelta(minutes=duration_minutes)
    slots: List[Dict[str, str]] = []
    for ini_l, fim_l in livres:
        atual = ini_l
        while atual + duracao <= fim_l:
            slots.append({"start": atual.isoformat(), "end": (atual + duracao).isoformat()})
            atual += duracao

    logger.info(
        f"[GCAL FREEBUSY] tenant={tenant_id} cal='{cal}' "
        f"{len(fundidos)} bloco(s) ocupado(s) -> {len(slots)} slot(s) de {duration_minutes}min."
    )
    return slots


async def create_event(
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    summary: str,
    description: str = "",
    calendar_id: Optional[str] = None,
) -> Dict[str, Any]:
    servico, cal_padrao = await _servico_do_tenant(tenant_id)
    cal = calendar_id or cal_padrao
    corpo = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }

    def _criar():
        return servico.events().insert(calendarId=cal, body=corpo).execute()

    evento = await asyncio.to_thread(_criar)
    logger.info(f"[GCAL EVENTO CRIADO] tenant={tenant_id} id={evento.get('id')} '{summary}'")
    return {"event_id": evento.get("id"), "html_link": evento.get("htmlLink"), "status": evento.get("status")}


async def cancel_event(tenant_id: uuid.UUID, event_id: str, calendar_id: Optional[str] = None) -> bool:
    """
    Remove o evento. Devolve True também quando ele já não existe (410/404):
    o estado desejado — 'não está mais na agenda' — foi alcançado, e tratar
    isso como erro faria o cancelamento falhar em retentativa.
    """
    servico, cal_padrao = await _servico_do_tenant(tenant_id)
    cal = calendar_id or cal_padrao

    def _apagar():
        servico.events().delete(calendarId=cal, eventId=event_id).execute()

    try:
        await asyncio.to_thread(_apagar)
        logger.info(f"[GCAL EVENTO CANCELADO] tenant={tenant_id} id={event_id}")
        return True
    except Exception as exc:
        texto = str(exc)
        if "410" in texto or "404" in texto or "deleted" in texto.lower():
            logger.info(f"[GCAL EVENTO] id={event_id} já não existia; tratando como cancelado.")
            return True
        logger.error(f"[GCAL EVENTO] Falha ao cancelar id={event_id}: {exc}")
        raise
