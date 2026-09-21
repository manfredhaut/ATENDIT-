"""
Adaptador Microsoft 365 (Graph) — espelha a interface do google_adapter.

    iniciar_consentimento(tenant_id)              -> url
    conferir_state(state)                         -> uuid.UUID
    concluir_consentimento(tenant_id, state, code)-> dict
    email_da_conta(tenant_id)                     -> str
    list_free_slots / create_event / cancel_event

DIFERENÇA QUE IMPORTA EM RELAÇÃO AO GOOGLE: o access token da Microsoft dura
cerca de 1 hora e o SDK do Google renovava sozinho. Aqui não há SDK — a
renovação é explícita em `_token_valido()`, que troca o refresh_token por um
access token novo quando o atual está perto de expirar e REGRAVA o resultado.
Sem isso, a conexão pararia de funcionar uma hora depois de conectada, e o
sintoma seria "a agenda parou" sem nenhum erro de configuração à vista.

O escopo inclui `offline_access` — sem ele a Microsoft não devolve
refresh_token, e cairíamos exatamente nessa parede de 1 hora.
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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import cifrar_dict, decifrar_dict
from app.core.database import AsyncSessionLocal
from app.models.scheduling import CalendarConnection

logger = logging.getLogger("atendit.microsoft_calendar")

PROVIDER = "microsoft"

AUTORIDADE = "https://login.microsoftonline.com/common"
AUTH_URI = f"{AUTORIDADE}/oauth2/v2.0/authorize"
TOKEN_URI = f"{AUTORIDADE}/oauth2/v2.0/token"
GRAPH = "https://graph.microsoft.com/v1.0"

ESCOPOS = ["offline_access", "openid", "profile", "email", "Calendars.ReadWrite"]

TTL_PKCE_SEGUNDOS = 1800
# Renova com folga: pedir um token que expira em 30s daria erro no meio da
# chamada seguinte.
MARGEM_RENOVACAO_SEGUNDOS = 300


class ConsentimentoExpirado(RuntimeError):
    """O code_verifier do PKCE não está mais no Redis — fluxo precisa recomeçar."""


class FalhaMicrosoft(RuntimeError):
    pass


def _redirect_uri() -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/calendar/oauth/{PROVIDER}/callback"


def _credenciais_app() -> Tuple[str, str]:
    cid = (settings.MICROSOFT_OAUTH_CLIENT_ID or "").strip()
    seg = (settings.MICROSOFT_OAUTH_CLIENT_SECRET or "").strip()
    if not cid or not seg:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth da Microsoft não configurado (MICROSOFT_OAUTH_CLIENT_ID/SECRET ausentes).",
        )
    return cid, seg


# --------------------------------------------------------------------------
# state assinado (mesmo esquema do Google)
# --------------------------------------------------------------------------
def _assinar_state(tenant_id: str) -> str:
    chave = (settings.INTERNAL_API_TOKEN or "").encode()
    return f"{tenant_id}.{hmac.new(chave, tenant_id.encode(), hashlib.sha256).hexdigest()[:32]}"


def conferir_state(state: str) -> uuid.UUID:
    try:
        tenant_id, _ = state.rsplit(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Parâmetro 'state' malformado.")
    if not hmac.compare_digest(_assinar_state(tenant_id), state):
        logger.warning("[MSCAL OAUTH] state com assinatura inválida — callback recusado.")
        raise HTTPException(status_code=400, detail="Assinatura do 'state' inválida.")
    try:
        return uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="'state' não contém um UUID válido.")


# --------------------------------------------------------------------------
# PKCE
# --------------------------------------------------------------------------
def _chave_pkce(state: str) -> str:
    return f"oauth:pkce:ms:{state}"


async def _cliente_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def _fechar(c) -> None:
    try:
        await c.aclose()
    except AttributeError:
        await c.close()


async def _guardar_verifier(state: str, verifier: str) -> None:
    c = await _cliente_redis()
    try:
        await c.setex(_chave_pkce(state), TTL_PKCE_SEGUNDOS, verifier)
    finally:
        await _fechar(c)


async def _recuperar_verifier(state: str) -> Optional[str]:
    c = await _cliente_redis()
    try:
        return await c.get(_chave_pkce(state))
    finally:
        await _fechar(c)


async def _descartar_verifier(state: str) -> None:
    c = await _cliente_redis()
    try:
        await c.delete(_chave_pkce(state))
    finally:
        await _fechar(c)


def _novo_verifier() -> Tuple[str, str]:
    import base64
    import secrets

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, desafio


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _http(url: str, metodo: str = "GET", token: Optional[str] = None,
          corpo: Optional[dict] = None, form: Optional[dict] = None) -> Dict[str, Any]:
    cabecalhos = {"Accept": "application/json", "User-Agent": "ATENDIT-Core/1.0"}
    dados = None
    if form is not None:
        dados = urllib.parse.urlencode(form).encode()
        cabecalhos["Content-Type"] = "application/x-www-form-urlencoded"
    elif corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=dados, method=metodo, headers=cabecalhos)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            texto = r.read().decode("utf-8")
            return json.loads(texto) if texto else {}
    except urllib.error.HTTPError as e:
        bruto = e.read().decode("utf-8", errors="replace")
        try:
            detalhe = json.loads(bruto)
            msg = (detalhe.get("error_description")
                   or (detalhe.get("error") or {}).get("message")
                   or bruto)
        except Exception:
            msg = bruto
        raise FalhaMicrosoft(f"Microsoft respondeu HTTP {e.code}: {str(msg)[:300]}")
    except Exception as exc:
        raise FalhaMicrosoft(f"Não consegui falar com a Microsoft: {exc}")


# --------------------------------------------------------------------------
# Consentimento
# --------------------------------------------------------------------------
async def iniciar_consentimento(tenant_id: uuid.UUID) -> str:
    cid, _ = _credenciais_app()
    state = _assinar_state(str(tenant_id))
    verifier, desafio = _novo_verifier()
    await _guardar_verifier(state, verifier)

    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "response_mode": "query",
        "scope": " ".join(ESCOPOS),
        "state": state,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        # 'consent' força a tela toda vez; sem isso, uma reconexão pode não
        # devolver refresh_token novo, e a agenda pararia em 1 hora.
        "prompt": "consent",
    }
    return f"{AUTH_URI}?{urllib.parse.urlencode(params)}"


async def concluir_consentimento(tenant_id: uuid.UUID, state: str, code: str) -> Dict[str, Any]:
    verifier = await _recuperar_verifier(state)
    if not verifier:
        raise ConsentimentoExpirado("code_verifier ausente ou expirado.")

    cid, seg = _credenciais_app()
    form = {
        "client_id": cid,
        "client_secret": seg,
        "code": code,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "scope": " ".join(ESCOPOS),
    }
    token = await asyncio.to_thread(_http, TOKEN_URI, "POST", None, None, form)

    if not token.get("access_token"):
        raise FalhaMicrosoft(f"A Microsoft não devolveu access_token: {str(token)[:200]}")

    dados = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "expira_em": (datetime.now(timezone.utc)
                      + timedelta(seconds=int(token.get("expires_in", 3600)))).isoformat(),
        "scopes": (token.get("scope") or "").split(),
    }

    email = None
    try:
        eu = await asyncio.to_thread(_http, f"{GRAPH}/me", "GET", dados["access_token"])
        email = eu.get("mail") or eu.get("userPrincipalName")
        dados["email"] = email
    except Exception as exc:
        logger.warning(f"[MSCAL] Conectado, mas não obtive o e-mail: {exc}")

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

    await _descartar_verifier(state)
    logger.info(
        f"[MSCAL OAUTH SUCESSO] Tenant {tenant_id} conectado como {email}. "
        f"refresh_token presente: {bool(dados.get('refresh_token'))}."
    )
    return {"tem_refresh_token": bool(dados.get("refresh_token")), "email": email}


# --------------------------------------------------------------------------
# Token com renovação
# --------------------------------------------------------------------------
async def _token_valido(tenant_id: uuid.UUID) -> str:
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
            raise FalhaMicrosoft(f"Inquilino {tenant_id} não tem Microsoft 365 conectado.")
        dados = decifrar_dict(conexao.credentials_encrypted)
        conexao_id = conexao.id

    try:
        expira = datetime.fromisoformat(dados.get("expira_em"))
    except Exception:
        expira = datetime.now(timezone.utc)

    if expira - timedelta(seconds=MARGEM_RENOVACAO_SEGUNDOS) > datetime.now(timezone.utc):
        return dados["access_token"]

    refresh = dados.get("refresh_token")
    if not refresh:
        raise FalhaMicrosoft(
            "O access token expirou e não há refresh_token guardado. "
            "Reconecte a conta Microsoft."
        )

    cid, seg = _credenciais_app()
    novo = await asyncio.to_thread(
        _http, TOKEN_URI, "POST", None, None,
        {"client_id": cid, "client_secret": seg, "refresh_token": refresh,
         "grant_type": "refresh_token", "scope": " ".join(ESCOPOS)},
    )
    dados["access_token"] = novo["access_token"]
    # A Microsoft pode devolver um refresh_token NOVO e invalidar o anterior;
    # manter o antigo quebraria a proxima renovacao.
    dados["refresh_token"] = novo.get("refresh_token") or refresh
    dados["expira_em"] = (datetime.now(timezone.utc)
                          + timedelta(seconds=int(novo.get("expires_in", 3600)))).isoformat()

    async with AsyncSessionLocal() as sessao:
        c = await sessao.get(CalendarConnection, conexao_id)
        c.credentials_encrypted = cifrar_dict(dados)
        await sessao.commit()

    logger.info(f"[MSCAL] Access token renovado para {tenant_id}.")
    return dados["access_token"]


async def email_da_conta(tenant_id: uuid.UUID) -> Optional[str]:
    """
    E-mail da conta conectada.

    NAO usa /me: nao pedimos o escopo User.Read, e em conta pessoal esse
    endpoint devolve 403 UnknownError. O dono aparece em /me/calendar, que
    o escopo Calendars.ReadWrite ja cobre -- medido em 2026-09-01, com /me
    falhando e /me/calendar respondendo normalmente.
    """
    token = await _token_valido(tenant_id)
    try:
        cal = await asyncio.to_thread(_http, f"{GRAPH}/me/calendar", "GET", token)
        dono = cal.get("owner") or {}
        return dono.get("address") or dono.get("name")
    except Exception as exc:
        logger.warning(f"[MSCAL] Nao obtive o e-mail da conta: {exc}")
        return None


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
    Janelas livres a partir de /me/calendarView.

    POR QUE NAO getSchedule: ele exige o ENDERECO da caixa, que so se obtem
    em /me -- e /me devolve 403 sem o escopo User.Read, que nao pedimos.
    Alem disso getSchedule e recurso de conta corporativa; em conta pessoal
    nao ha caixa a consultar. calendarView funciona nos dois tipos e usa
    apenas Calendars.ReadWrite, que ja temos.

    A semantica final e a mesma do Google: um slot so entra se couber
    INTEIRO numa janela livre -- comparar apenas o inicio deixaria passar
    horario que comeca livre e termina sobre um compromisso.
    """
    inicio, fim = date_range
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=timezone.utc)
    if duration_minutes <= 0:
        raise ValueError("duration_minutes deve ser maior que zero.")

    token = await _token_valido(tenant_id)

    parametros = urllib.parse.urlencode({
        "startDateTime": inicio.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "endDateTime": fim.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "$select": "subject,start,end,isCancelled,showAs",
        "$top": "200",
        "$orderby": "start/dateTime",
    })
    resposta = await asyncio.to_thread(
        _http, f"{GRAPH}/me/calendarView?{parametros}", "GET", token
    )

    ocupados: List[Tuple[datetime, datetime]] = []
    for ev in resposta.get("value", []) or []:
        if ev.get("isCancelled"):
            continue
        # 'free' e 'workingElsewhere' nao bloqueiam a agenda -- mesma regra
        # que o freebusy do Google aplica sozinho.
        if (ev.get("showAs") or "").lower() in ("free", "workingelsewhere"):
            continue
        try:
            ini_e = datetime.fromisoformat(ev["start"]["dateTime"][:26]).replace(tzinfo=timezone.utc)
            fim_e = datetime.fromisoformat(ev["end"]["dateTime"][:26]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        ocupados.append((ini_e, fim_e))
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
        f"[MSCAL CALENDARVIEW] tenant={tenant_id} "
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
    token = await _token_valido(tenant_id)
    corpo = {
        "subject": summary,
        "body": {"contentType": "text", "content": description},
        "start": {"dateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
    }
    ev = await asyncio.to_thread(_http, f"{GRAPH}/me/events", "POST", token, corpo)
    logger.info(f"[MSCAL EVENTO CRIADO] tenant={tenant_id} id={ev.get('id')} '{summary}'")
    return {"event_id": ev.get("id"), "html_link": ev.get("webLink"), "status": "confirmed"}


async def cancel_event(tenant_id: uuid.UUID, event_id: str, calendar_id: Optional[str] = None) -> bool:
    """
    Remove o evento. True também quando ele já não existe (404/410): o estado
    desejado foi alcançado, e tratar como erro faria a retentativa falhar.
    """
    token = await _token_valido(tenant_id)
    try:
        await asyncio.to_thread(_http, f"{GRAPH}/me/events/{event_id}", "DELETE", token)
        logger.info(f"[MSCAL EVENTO CANCELADO] tenant={tenant_id} id={event_id}")
        return True
    except FalhaMicrosoft as exc:
        texto = str(exc)
        if "404" in texto or "410" in texto or "ErrorItemNotFound" in texto:
            logger.info(f"[MSCAL EVENTO] id={event_id} já não existia; tratando como cancelado.")
            return True
        logger.error(f"[MSCAL EVENTO] Falha ao cancelar id={event_id}: {exc}")
        raise
