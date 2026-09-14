"""
Rotas de calendário — camada GENÉRICA por provedor.

Estrutura pensada para não ser reescrita quando Microsoft 365, CalDAV e
Calendly entrarem:

    GET  /calendar/oauth/{provider}/start?tenant=<slug|uuid>
    GET  /calendar/oauth/{provider}/callback
    GET  /calendar/status/{tenant}
    POST /calendar/disconnect/{tenant}
    GET  /calendar/appointments/{tenant}

O provedor é um parâmetro de caminho resolvido contra ADAPTADORES. Provedor
conhecido mas ainda não implementado devolve 501 com mensagem clara, em vez
de 404 — a diferença entre "não existe" e "ainda não construímos" importa
para quem está no painel.

✅ AUTENTICAÇÃO (desde 2026-09-01): start, status, disconnect e
appointments exigem sessão de admin OU de tenant, e a de tenant só vale para
o PRÓPRIO inquilino (ver _exigir_dono). O callback do OAuth e o webhook do
Calendly seguem abertos por necessidade — quem os chama é o provedor, que não
tem cookie; eles se protegem por `state` assinado e HMAC.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.crypto import decifrar_dict
from app.services import template_service
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Appointment, CalendarConnection
from app.models.tenant import Tenant
from app.services.calendar import calendly_adapter, google_adapter, microsoft_adapter

logger = logging.getLogger("atendit.calendar_routes")

router = APIRouter(prefix="/calendar", tags=["Calendário"])

# Provedores previstos no produto. None = ainda não implementado.
ADAPTADORES: Dict[str, Any] = {
    "google": google_adapter,
    "microsoft": microsoft_adapter,
    "caldav": None,
    "calendly": calendly_adapter,
}

ROTULOS = {
    "google": "Google Calendar",
    "microsoft": "Microsoft 365",
    "caldav": "CalDAV / iCal",
    "calendly": "Calendly",
}


def _adaptador(provider: str):
    p = (provider or "").lower()
    if p not in ADAPTADORES:
        raise HTTPException(status_code=404, detail=f"Provedor de calendário desconhecido: '{provider}'.")
    adaptador = ADAPTADORES[p]
    if adaptador is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{ROTULOS.get(p, p)} ainda não está disponível. Use o Google Calendar por enquanto.",
        )
    return adaptador


async def _exigir_dono(request, identificador: str):
    """
    Autoriza a requisição para o inquilino de `identificador` (slug ou UUID).

    🔴 Fechado em 2026-09-01. Até então TODA esta família de rotas era
    anônima — o cabeçalho deste arquivo dizia "SEM AUTENTICAÇÃO, de propósito,
    no modo desenvolvimento", e a consequência foi medida no dia:

      GET /calendar/status/manitest       -> 200 SEM cookie nenhum, listando
                                             as agendas conectadas do cliente
      GET /calendar/appointments/{tenant} -> nome e telefone de quem agendou
      POST /calendar/disconnect/{tenant}  -> qualquer um derruba a agenda de
                                             qualquer cliente

    Admin continua alcançando qualquer inquilino; sessão de tenant só o dela.

    ⚠️ O CALLBACK do OAuth e o webhook do Calendly ficam de FORA desta regra,
    e têm de ficar: quem os chama é o Google/Microsoft/Calendly, que não têm
    cookie nenhum. Eles se protegem pelo `state` assinado e pela assinatura
    HMAC do webhook.
    """
    from app.core import autorizacao as _autz

    tenant_id, slug = await resolver_tenant(identificador)
    await _autz.exigir_acesso_ao_tenant(request, tenant_id=tenant_id)
    return tenant_id, slug


async def resolver_tenant(identificador: str) -> tuple:
    """
    Aceita UUID ou slug e devolve (uuid, slug).

    O painel é servido em /{tenant_slug} e só conhece o slug; as tabelas usam
    UUID. Sem esta ponte, o botão do painel teria de saber o UUID, que é
    exatamente o detalhe técnico que o cliente final não pode ver.
    """
    try:
        tid = uuid.UUID(identificador)
        campo = Tenant.id == tid
    except ValueError:
        campo = Tenant.slug == identificador

    async with AsyncSessionLocal() as sessao:
        tenant = (await sessao.execute(select(Tenant).where(campo))).scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Inquilino '{identificador}' não localizado.")
    return tenant.id, tenant.slug


def _voltar_ao_painel(slug: str, **params: str) -> RedirectResponse:
    """
    Redireciona de volta ao painel do inquilino com o resultado na query.

    Não redireciona para /dashboards/calendar_config: aquela rota devolve o
    HTML embrulhado em JSON, e o navegador mostraria código-fonte escapado
    em vez de página. O painel real é /{slug}.
    """
    consulta = "&".join(f"{k}={v}" for k, v in params.items())
    destino = f"/{slug}?{consulta}" if consulta else f"/{slug}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# OAuth genérico
# --------------------------------------------------------------------------
@router.get("/oauth/{provider}/start", include_in_schema=False)
async def iniciar_oauth(
    provider: str,
    request: Request,
    tenant: str = Query(..., description="slug ou UUID do inquilino"),
):
    adaptador = _adaptador(provider)
    tenant_id, slug = await _exigir_dono(request, tenant)
    try:
        url = await adaptador.iniciar_consentimento(tenant_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[CAL OAUTH] Falha ao montar consentimento ({provider}/{slug}): {exc}")
        return _voltar_ao_painel(slug, calendario="erro", motivo="config")
    logger.info(f"[CAL OAUTH] Início do consentimento {provider} para '{slug}'.")
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/oauth/{provider}/callback", include_in_schema=False)
async def callback_oauth(
    provider: str,
    state: str = Query(""),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    adaptador = _adaptador(provider)

    # O slug só é conhecido depois de validar o state; até lá, se algo falhar,
    # não há painel para onde voltar sem confiar em dado não verificado.
    try:
        tenant_id = adaptador.conferir_state(state)
    except HTTPException:
        raise
    _, slug = await resolver_tenant(str(tenant_id))

    if error:
        # A Microsoft as vezes dispara um SEGUNDO callback com error=server_error
        # logo depois de um consentimento BEM-SUCEDIDO. Sem esta checagem, esse
        # callback tardio sobrescrevia a mensagem de sucesso e o painel dizia
        # "erro" com a conta ja conectada -- observado em 2026-09-01 09:42.
        async with AsyncSessionLocal() as sessao:
            ja = (
                await sessao.execute(
                    select(CalendarConnection).where(
                        CalendarConnection.tenant_id == tenant_id,
                        CalendarConnection.provider == provider.lower(),
                        CalendarConnection.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if ja is not None and ja.credentials_encrypted:
            logger.info(
                f"[CAL OAUTH] '{slug}' recebeu '{error}' em {provider}, mas a conexao "
                f"ja esta ativa; tratando como sucesso."
            )
            return _voltar_ao_painel(slug, calendario="conectado", provider=provider)

        logger.warning(f"[CAL OAUTH] '{slug}' recusou/cancelou em {provider}: {error}")
        motivo = "recusado" if error in ("access_denied",) else "erro"
        return _voltar_ao_painel(slug, calendario=motivo, provider=provider)

    if not code:
        return _voltar_ao_painel(slug, calendario="erro", motivo="sem_code", provider=provider)

    try:
        await adaptador.concluir_consentimento(tenant_id, state=state, code=code)
    except adaptador.ConsentimentoExpirado:
        logger.warning(f"[CAL OAUTH] Consentimento expirado para '{slug}'.")
        return _voltar_ao_painel(slug, calendario="expirado", provider=provider)
    except Exception as exc:
        logger.error(f"[CAL OAUTH] Falha ao concluir consentimento ({provider}/{slug}): {exc}")
        return _voltar_ao_painel(slug, calendario="erro", motivo="token", provider=provider)

    logger.info(f"[CAL OAUTH SUCESSO] '{slug}' conectado em {provider}.")
    return _voltar_ao_painel(slug, calendario="conectado", provider=provider)


# --------------------------------------------------------------------------
# Status e desconexão
# --------------------------------------------------------------------------
ORDEM_PREFERENCIA = ("google", "microsoft", "caldav", "calendly", "local")


@router.get("/status/{tenant}", include_in_schema=False)
async def status_conexao(
    tenant: str,
    request: Request,
    provider: Optional[str] = Query(None, description="google|calendly|..."),
):
    """
    Status da conexao.

    Sem 'provider', devolve a primeira ativa segundo ORDEM_PREFERENCIA e
    lista todas em 'conexoes'. Desde que um inquilino pode ter mais de uma,
    "a conexao do tenant" deixou de existir como conceito.
    """
    tenant_id, slug = await _exigir_dono(request, tenant)

    async with AsyncSessionLocal() as sessao:
        filtros = [CalendarConnection.tenant_id == tenant_id]
        if provider:
            filtros.append(CalendarConnection.provider == provider.lower())
        linhas = (await sessao.execute(select(CalendarConnection).where(*filtros))).scalars().all()

    ativas = [c for c in linhas if c.is_active and c.credentials_encrypted]
    todas = [
        {
            "provider": c.provider,
            "provider_label": ROTULOS.get(c.provider, c.provider),
            "ativa": bool(c.is_active and c.credentials_encrypted),
            "calendar_id": c.calendar_id,
            "atualizado_em": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in linhas
    ]

    if not ativas:
        return {"conectado": False, "tenant_slug": slug, "provider": None,
                "email": None, "conexoes": todas}

    conexao = min(
        ativas,
        key=lambda c: ORDEM_PREFERENCIA.index(c.provider) if c.provider in ORDEM_PREFERENCIA else 99,
    )

    resultado: Dict[str, Any] = {
        "conectado": True,
        "tenant_slug": slug,
        "conexoes": todas,
        "provider": conexao.provider,
        "provider_label": ROTULOS.get(conexao.provider, conexao.provider),
        "calendar_id": conexao.calendar_id,
        "atualizado_em": conexao.updated_at.isoformat() if conexao.updated_at else None,
        "email": None,
    }

    try:
        dados = decifrar_dict(conexao.credentials_encrypted)
        resultado["credencial_legivel"] = True
        resultado["tem_refresh_token"] = bool(dados.get("refresh_token"))
        resultado["escopos"] = dados.get("scopes")
    except Exception as exc:
        # Credencial ilegível = conexão morta na prática. O painel precisa
        # mostrar "reconecte", não "conectado".
        logger.error(f"[CAL STATUS] Credencial ilegível para '{slug}': {exc}")
        return {
            "conectado": False,
            "tenant_slug": slug,
            "provider": conexao.provider,
            "email": None,
            "erro": "credencial_ilegivel",
        }

    adaptador = ADAPTADORES.get(conexao.provider)
    if adaptador is not None and hasattr(adaptador, "email_da_conta"):
        try:
            resultado["email"] = await adaptador.email_da_conta(tenant_id)
        except Exception as exc:
            # Não derruba o status: o painel mostra "conectado" mesmo sem o
            # e-mail, que é enfeite, não requisito.
            logger.warning(f"[CAL STATUS] Não obtive o e-mail da conta de '{slug}': {exc}")

    return resultado


@router.post("/disconnect/{tenant}", include_in_schema=False)
async def desconectar(
    tenant: str,
    request: Request,
    provider: Optional[str] = Query(None, description="google|calendly|..."),
):
    """
    Desativa a conexao e APAGA as credenciais cifradas.

    Sem 'provider', desconecta TODAS. E explicito de proposito: com varios
    provedores possiveis, desconectar "a conexao" sem dizer qual poderia
    derrubar em silencio um provedor que o operador nem lembrava que tinha.
    """
    tenant_id, slug = await _exigir_dono(request, tenant)

    async with AsyncSessionLocal() as sessao:
        filtros = [CalendarConnection.tenant_id == tenant_id]
        if provider:
            filtros.append(CalendarConnection.provider == provider.lower())
        linhas = (await sessao.execute(select(CalendarConnection).where(*filtros))).scalars().all()
        if not linhas:
            return {"desconectado": True, "observacao": "não havia conexão"}

        removidos = []
        for conexao in linhas:
            # Apagar o token e nao so marcar inativo: credencial guardada sem
            # uso e superficie de vazamento sem contrapartida.
            conexao.credentials_encrypted = None
            conexao.is_active = False
            conexao.sync_token = None
            removidos.append(conexao.provider)
        await sessao.commit()

    logger.info(f"[CAL] Conexoes removidas para '{slug}': {removidos}")
    return {"desconectado": True, "tenant_slug": slug, "provedores": removidos}


# --------------------------------------------------------------------------
# Compromissos (consumido pela grade do painel)
# --------------------------------------------------------------------------
@router.get("/appointments/{tenant}", include_in_schema=False)
async def listar_compromissos(
    tenant: str,
    request: Request,
    inicio: Optional[str] = Query(None, description="ISO-8601; padrão: hoje"),
    fim: Optional[str] = Query(None, description="ISO-8601; padrão: início + 30 dias"),
):
    tenant_id, slug = await _exigir_dono(request, tenant)

    agora = datetime.now(timezone.utc)
    try:
        dt_ini = datetime.fromisoformat(inicio) if inicio else agora - timedelta(days=1)
        dt_fim = datetime.fromisoformat(fim) if fim else dt_ini + timedelta(days=30)
    except ValueError:
        raise HTTPException(status_code=400, detail="Datas devem estar em ISO-8601.")

    if dt_ini.tzinfo is None:
        dt_ini = dt_ini.replace(tzinfo=timezone.utc)
    if dt_fim.tzinfo is None:
        dt_fim = dt_fim.replace(tzinfo=timezone.utc)

    async with AsyncSessionLocal() as sessao:
        linhas = (
            await sessao.execute(
                select(Appointment)
                .where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.start_at >= dt_ini,
                    Appointment.start_at <= dt_fim,
                )
                .order_by(Appointment.start_at.asc())
            )
        ).scalars().all()

    return {
        "tenant_slug": slug,
        "inicio": dt_ini.isoformat(),
        "fim": dt_fim.isoformat(),
        "total": len(linhas),
        "appointments": [
            {
                "id": str(a.id),
                "start_at": a.start_at.isoformat(),
                "end_at": a.end_at.isoformat(),
                "status": a.status,
                "customer_name": a.customer_name,
                "customer_phone": a.customer_phone,
                "source": a.source,
                "external_event_id": a.external_event_id,
            }
            for a in linhas
        ],
    }


# --------------------------------------------------------------------------
# Réguas de mensagem
# --------------------------------------------------------------------------
# Mesma política das demais rotas do painel: sem X-Internal-Token, porque o
# navegador as chama. Passam a exigir sessão quando o login existir (PARTE 5).
@router.get("/message-templates/{tenant}", include_in_schema=False)
async def obter_templates(tenant: str):
    tenant_id, slug = await resolver_tenant(tenant)
    return {"tenant_slug": slug, "templates": await template_service.listar(tenant_id)}


@router.post("/message-templates/{tenant}", include_in_schema=False)
async def salvar_templates(tenant: str, payload: Dict[str, Any]):
    """
    Aceita {"templates": {"reminder": "texto", ...}} ou
    {"template_type": "reminder", "body": "texto"}.

    Corpo vazio LIMPA a customização e volta ao default -- e por isso o
    default nunca e gravado no banco: gravado, ele viraria "customizado"
    e deixaria de acompanhar futuras melhorias do texto padrao.
    """
    tenant_id, slug = await resolver_tenant(tenant)

    itens = payload.get("templates")
    if itens is None:
        tipo = payload.get("template_type")
        if not tipo:
            raise HTTPException(status_code=400, detail="Informe 'templates' ou 'template_type' e 'body'.")
        itens = {tipo: payload.get("body", "")}

    if not isinstance(itens, dict):
        raise HTTPException(status_code=400, detail="'templates' deve ser um objeto {tipo: texto}.")

    resultados = []
    for tipo, corpo in itens.items():
        if tipo not in template_service.TIPOS:
            raise HTTPException(
                status_code=400,
                detail=f"template_type inválido: '{tipo}'. Válidos: {list(template_service.TIPOS)}",
            )
        resultados.append(await template_service.salvar(tenant_id, tipo, corpo or ""))

    return {"tenant_slug": slug, "salvos": resultados,
            "templates": await template_service.listar(tenant_id)}


# --------------------------------------------------------------------------
# Calendly — conexão por token (PARTE A/B) e recepção de webhook (PARTE C)
# --------------------------------------------------------------------------
@router.post("/connections/calendly/{tenant}", include_in_schema=False)
async def conectar_calendly(tenant: str, payload: Dict[str, Any]):
    """Valida o token, registra o webhook no Calendly e grava tudo cifrado."""
    tenant_id, slug = await resolver_tenant(tenant)
    token = (payload.get("token") or "").strip()
    link = (payload.get("scheduling_link") or "").strip()

    if not token:
        raise HTTPException(status_code=400, detail="Informe o Personal Access Token do Calendly.")

    try:
        r = await calendly_adapter.conectar(tenant_id, token, link)
    except calendly_adapter.TokenInvalido as exc:
        # 401 com a mensagem ESPECIFICA do adaptador, nao um erro generico:
        # quem colou o token precisa saber que o problema e o token.
        raise HTTPException(status_code=401, detail=str(exc))
    except calendly_adapter.FalhaCalendly as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    r["tenant_slug"] = slug
    return r


@router.post("/webhook/calendly/{tenant_id}", include_in_schema=False)
async def webhook_calendly(tenant_id: uuid.UUID, request: Request):
    """
    Recebe invitee.created / invitee.canceled.

    A assinatura e validada ANTES de qualquer leitura util do corpo: payload
    forjado nao pode criar linha em appointments. Sem conexao ou sem
    signing_key, recusa -- falha fechado.
    """
    corpo_bruto = await request.body()

    try:
        seg = await calendly_adapter.segredos(tenant_id)
    except Exception as exc:
        logger.error(f"[CALENDLY WEBHOOK] Não consegui ler os segredos de {tenant_id}: {exc}")
        raise HTTPException(status_code=503, detail="Conexão Calendly indisponível.")

    chave = (seg or {}).get("signing_key")
    if not chave:
        logger.warning(f"[CALENDLY WEBHOOK] {tenant_id} sem signing_key; recusado.")
        raise HTTPException(status_code=403, detail="Nenhuma conexão Calendly ativa para este inquilino.")

    if not calendly_adapter.verificar_assinatura(
        chave, request.headers.get("calendly-webhook-signature"), corpo_bruto
    ):
        logger.warning(f"[CALENDLY WEBHOOK] Assinatura inválida para {tenant_id}; recusado.")
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        dados = json.loads(corpo_bruto or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Corpo não é JSON válido.")

    resultado = await calendly_adapter.processar_evento(tenant_id, dados)
    logger.info(f"[CALENDLY WEBHOOK] {tenant_id}: {resultado}")
    return resultado

# ============================================================================
# ENDPOINTS DO CENTRO NERVOSO: CRM, ENCAIXES E MOTOR AS-IS / TO-BE
# ============================================================================
from pydantic import BaseModel

class NovoEncaixeRequest(BaseModel):
    customer_name: str
    customer_phone: str
    start_at: str
    duration_minutes: int = 45

@router.post("/appointments/{tenant}", include_in_schema=False)
async def criar_encaixe_manual(tenant: str, request: Request, payload: NovoEncaixeRequest):
    tenant_id, slug = await _exigir_dono(request, tenant)
    try:
        dt_ini = datetime.fromisoformat(payload.start_at)
        if dt_ini.tzinfo is None:
            dt_ini = dt_ini.replace(tzinfo=timezone.utc)
        dt_fim = dt_ini + timedelta(minutes=payload.duration_minutes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data/hora inválida: {e}")

    async with AsyncSessionLocal() as sessao:
        novo = Appointment(
            tenant_id=tenant_id,
            customer_name=payload.customer_name.strip(),
            customer_phone=payload.customer_phone.strip(),
            start_at=dt_ini,
            end_at=dt_fim,
            status="confirmed",
            provider="manual",
            source="painel_cockpit"
        )
        sessao.add(novo)
        await sessao.commit()
        await sessao.refresh(novo)

    logger.info(f"[ENCAIXE] Novo compromisso criado no cockpit para {payload.customer_name} às {dt_ini}")
    return {"success": True, "id": str(novo.id), "start_at": novo.start_at.isoformat()}


@router.get("/crm-summary/{tenant}", include_in_schema=False)
async def obter_resumo_crm(tenant: str, request: Request):
    tenant_id, slug = await _exigir_dono(request, tenant)
    agora = datetime.now(timezone.utc)
    hoje_ini = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    hoje_fim = hoje_ini + timedelta(days=1)
    semana_fim = hoje_ini + timedelta(days=7)
    mes_ini = hoje_ini.replace(day=1)
    mes_fim = (mes_ini + timedelta(days=32)).replace(day=1)

    async with AsyncSessionLocal() as sessao:
        # 1. Total Hoje
        res_hoje = await sessao.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.start_at >= hoje_ini,
                Appointment.start_at < hoje_fim
            )
        )
        apts_hoje = res_hoje.scalars().all()

        # 2. Total Semana
        res_sem = await sessao.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.start_at >= hoje_ini,
                Appointment.start_at < semana_fim
            )
        )
        apts_sem = res_sem.scalars().all()

        # 3. Total Mês
        res_mes = await sessao.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.start_at >= mes_ini,
                Appointment.start_at < mes_fim
            )
        )
        apts_mes = res_mes.scalars().all()

        # 4. Desistências / Cancelados no mês
        res_cancel = await sessao.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.start_at >= mes_ini,
                Appointment.status.in_(["canceled", "cancelado", "desistencia", "no_show"])
            )
        )
        cancelados = res_cancel.scalars().all()

    return {
        "hoje": len(apts_hoje),
        "semana": len(apts_sem),
        "mes": len(apts_mes),
        "desistencias": len(cancelados),
        "apts_hoje_detalhe": [
            {
                "id": str(a.id),
                "hora": a.start_at.strftime("%H:%M") if a.start_at else "--:--",
                "cliente": a.customer_name or a.customer_phone or "Cliente",
                "fone": a.customer_phone or "",
                "status": a.status or "confirmed",
                "provedor": a.provider or "local"
            }
            for a in apts_hoje
        ]
    }
