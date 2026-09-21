"""
Motor de agendamento — regra de negócio, independente de provedor.

Quem chama (webhook, painel) fala só com este módulo. Ele decide se há
calendário externo conectado e, se houver, cruza com ele. O webhook NÃO
sabe qual provedor está ligado.

--------------------------------------------------------------------------
DECISÕES QUE PRECISAM SOBREVIVER À TROCA DE SESSÃO
--------------------------------------------------------------------------
1. FUSO. business_hours guarda hora SEM fuso ("09:00"). A tabela tenants não
   tem coluna de fuso, então assumo America/Sao_Paulo (FUSO_PADRAO). É
   assunção explícita, não descuido: quando houver inquilino fora do
   horário de Brasília, isto vira coluna em tenants.

2. CONDIÇÃO DE CORRIDA. Dois clientes pedindo o mesmo horário ao mesmo
   tempo NÃO são resolvidos em Python — verificar-e-depois-inserir tem
   janela entre as duas operações, e nenhum nível de isolamento do
   Postgres impede insert fantasma em READ COMMITTED.
   Quem resolve é o banco, com EXCLUDE USING gist sobre
   (tenant_id, service_type_id, tstzrange(start_at, end_at)) filtrado por
   status='confirmed'. O segundo INSERT viola a restrição e vira
   HorarioIndisponivel.
   ⚠️ Isso é a POLÍTICA CONSERVADORA: nunca há dupla marcação. Se o
   negócio quiser overbooking (encaixe, fila no consultório), é mudança
   de produto e a restrição precisa ser revista — não mude só o código.

3. GOOGLE FALHA DEPOIS DO INSERT. O compromisso local é MANTIDO, com
   provider='local' e external_event_id nulo, e a resposta declara
   sincronizado_google=False. Apagar seria pior: o cliente já ouviu
   "marcado". O que não pode existir é registro local dizendo que está no
   Google quando não está.
"""

import logging
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.scheduling import (
    Appointment,
    BusinessHours,
    CalendarConnection,
    ServiceType,
    Waitlist,
)
from app.services.calendar import google_adapter, microsoft_adapter

logger = logging.getLogger("atendit.scheduling")

FUSO_PADRAO = ZoneInfo("America/Sao_Paulo")

ADAPTADORES = {"google": google_adapter, "microsoft": microsoft_adapter}


class HorarioIndisponivel(RuntimeError):
    """O horário pedido não está livre (ou foi tomado entre a consulta e a reserva)."""


class ServicoNaoEncontrado(RuntimeError):
    pass


class CompromissoNaoEncontrado(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Carga de configuração
# --------------------------------------------------------------------------
async def _carregar_servico(
    sessao: AsyncSession, tenant_id: uuid.UUID, service_type_id: Optional[uuid.UUID] = None,
    nome: Optional[str] = None,
) -> ServiceType:
    filtros = [ServiceType.tenant_id == tenant_id, ServiceType.is_active.is_(True)]
    if service_type_id:
        filtros.append(ServiceType.id == service_type_id)
    elif nome:
        filtros.append(ServiceType.name.ilike(f"%{nome}%"))

    servicos = (await sessao.execute(select(ServiceType).where(and_(*filtros)))).scalars().all()
    if not servicos:
        raise ServicoNaoEncontrado(
            f"Nenhum serviço ativo encontrado para o inquilino"
            + (f" com nome parecido com '{nome}'." if nome else ".")
        )
    return servicos[0]


async def _janelas_do_dia(
    sessao: AsyncSession, tenant_id: uuid.UUID, service_type_id: uuid.UUID, dia: date
) -> List[Tuple[time, time]]:
    """
    Horário comercial do dia. Regra vinda do schema: service_type_id NULO
    vale para todos os serviços; preenchido, restringe àquele serviço.
    A específica tem precedência sobre a genérica.
    """
    dow = dia.weekday()  # 0=segunda
    linhas = (
        await sessao.execute(
            select(BusinessHours).where(
                BusinessHours.tenant_id == tenant_id,
                BusinessHours.day_of_week == dow,
                BusinessHours.is_active.is_(True),
                or_(
                    BusinessHours.service_type_id == service_type_id,
                    BusinessHours.service_type_id.is_(None),
                ),
            )
        )
    ).scalars().all()

    especificas = [l for l in linhas if l.service_type_id is not None]
    usar = especificas or linhas
    return [(l.start_time, l.end_time) for l in usar]


# Provedores que sabem CONSULTAR e ESCREVER agenda, em ordem de preferencia.
# Calendly nao entra: e somente leitura, nao expoe freebusy nem cria evento.
# Um inquilino com Calendly + Google precisa cruzar disponibilidade com o
# Google; escolher o Calendly aqui deixaria o motor sem fonte de horarios.
PROVEDORES_COM_AGENDA = ("google", "microsoft")


async def _conexao_ativa(sessao: AsyncSession, tenant_id: uuid.UUID) -> Optional[CalendarConnection]:
    """
    Conexao utilizavel para agendamento.

    Desde que um inquilino pode ter mais de um provedor, isto deixou de ser
    "a linha do tenant" e passou a ser "a primeira linha de um provedor que
    sirva para agendar".
    """
    linhas = (
        await sessao.execute(
            select(CalendarConnection).where(
                CalendarConnection.tenant_id == tenant_id,
                CalendarConnection.is_active.is_(True),
                CalendarConnection.provider.in_(PROVEDORES_COM_AGENDA),
            )
        )
    ).scalars().all()

    por_provedor = {c.provider: c for c in linhas if c.credentials_encrypted}
    for provedor in PROVEDORES_COM_AGENDA:
        if provedor in por_provedor:
            return por_provedor[provedor]
    return None


# --------------------------------------------------------------------------
# Disponibilidade
# --------------------------------------------------------------------------
async def find_available_slots(
    tenant_id: uuid.UUID,
    service_type_id: Optional[uuid.UUID],
    date_range: Tuple[datetime, datetime],
    limite: int = 40,
) -> Dict[str, Any]:
    """
    Cruza business_hours + duração do serviço + compromissos locais +
    (se houver conexão) a agenda real do provedor.

    Sem calendário conectado, NÃO trava: cai para agenda local, usando só a
    tabela appointments. É o modo em que o produto precisa funcionar antes
    de o cliente conectar o Google.
    """
    inicio, fim = date_range
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=FUSO_PADRAO)
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=FUSO_PADRAO)

    async with AsyncSessionLocal() as sessao:
        servico = await _carregar_servico(sessao, tenant_id, service_type_id=service_type_id)
        duracao = timedelta(minutes=servico.duration_minutes)
        buffer = timedelta(minutes=servico.buffer_minutes)

        # 1) Candidatos dentro do horário comercial.
        candidatos: List[Tuple[datetime, datetime]] = []
        dia = inicio.astimezone(FUSO_PADRAO).date()
        ultimo_dia = fim.astimezone(FUSO_PADRAO).date()
        while dia <= ultimo_dia:
            for h_ini, h_fim in await _janelas_do_dia(sessao, tenant_id, servico.id, dia):
                cursor = datetime.combine(dia, h_ini, tzinfo=FUSO_PADRAO)
                limite_dia = datetime.combine(dia, h_fim, tzinfo=FUSO_PADRAO)
                while cursor + duracao <= limite_dia:
                    if cursor >= inicio and cursor + duracao <= fim:
                        candidatos.append((cursor, cursor + duracao))
                    cursor += duracao
            dia += timedelta(days=1)

        if not candidatos:
            # Mesmo formato do retorno normal: faltando 'total' e
            # 'service_type_id', quem consome quebra com KeyError num dia
            # sem horario comercial (domingo, feriado).
            return {
                "servico": servico.name,
                "service_type_id": str(servico.id),
                "duracao_minutos": servico.duration_minutes,
                "fonte": "sem_horario_comercial",
                "total": 0,
                "slots": [],
            }

        # 2) Remove o que já está marcado localmente, respeitando o buffer.
        marcados = (
            await sessao.execute(
                select(Appointment).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.service_type_id == servico.id,
                    Appointment.status == "confirmed",
                    Appointment.end_at >= inicio,
                    Appointment.start_at <= fim,
                )
            )
        ).scalars().all()

        def colide_local(ini: datetime, f: datetime) -> bool:
            for a in marcados:
                if ini < a.end_at + buffer and f + buffer > a.start_at:
                    return True
            return False

        candidatos = [(i, f) for i, f in candidatos if not colide_local(i, f)]

        conexao = await _conexao_ativa(sessao, tenant_id)

    # 3) Cruza com o provedor externo, se houver.
    fonte = "local"
    if conexao is not None:
        adaptador = ADAPTADORES.get(conexao.provider)
        if adaptador is None:
            logger.warning(f"[SCHED] Provedor '{conexao.provider}' sem adaptador; usando agenda local.")
        else:
            try:
                livres = await adaptador.list_free_slots(
                    tenant_id=tenant_id, date_range=(inicio, fim), duration_minutes=servico.duration_minutes
                )
                # Um candidato só sobrevive se couber INTEIRO numa janela livre
                # do provedor. Comparar só o início deixaria passar horário que
                # começa livre e termina em cima de um compromisso.
                janelas = [
                    (datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                    for s in livres
                ]
                def cabe(ini: datetime, f: datetime) -> bool:
                    return any(ini >= ji and f <= jf for ji, jf in janelas)
                candidatos = [(i, f) for i, f in candidatos if cabe(i, f)]
                fonte = conexao.provider
            except Exception as exc:
                # Degradar para local é melhor que recusar atendimento: o pior
                # caso é oferecer um horário que o Google já tinha ocupado, e
                # a reserva falha depois com mensagem clara.
                logger.error(f"[SCHED] Falha ao consultar '{conexao.provider}': {exc}. Caindo para agenda local.")
                fonte = "local_fallback"

    candidatos.sort()
    return {
        "servico": servico.name,
        "service_type_id": str(servico.id),
        "duracao_minutos": servico.duration_minutes,
        "fonte": fonte,
        "total": len(candidatos),
        "slots": [
            {"inicio": i.isoformat(), "fim": f.isoformat(),
             "rotulo": i.astimezone(FUSO_PADRAO).strftime("%d/%m %H:%M")}
            for i, f in candidatos[:limite]
        ],
    }


# --------------------------------------------------------------------------
# Reserva
# --------------------------------------------------------------------------
async def book_appointment(
    tenant_id: uuid.UUID,
    service_type_id: Optional[uuid.UUID],
    inicio: datetime,
    customer_phone: str,
    customer_name: Optional[str] = None,
    source: str = "whatsapp",
) -> Dict[str, Any]:
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=FUSO_PADRAO)

    async with AsyncSessionLocal() as sessao:
        servico = await _carregar_servico(sessao, tenant_id, service_type_id=service_type_id)
        fim = inicio + timedelta(minutes=servico.duration_minutes)

        if inicio < datetime.now(FUSO_PADRAO):
            raise HorarioIndisponivel("Não é possível marcar em horário que já passou.")

        compromisso = Appointment(
            tenant_id=tenant_id,
            service_type_id=servico.id,
            customer_phone=customer_phone,
            customer_name=customer_name,
            start_at=inicio,
            end_at=fim,
            status="confirmed",
            provider="local",
            source=source,
        )
        sessao.add(compromisso)
        try:
            await sessao.commit()
        except IntegrityError:
            await sessao.rollback()
            # A restrição de exclusão do banco pegou: alguém marcou este
            # intervalo entre a consulta de disponibilidade e agora.
            logger.warning(f"[SCHED] Colisão de horário em {inicio.isoformat()} (tenant {tenant_id}).")
            raise HorarioIndisponivel(
                "Esse horário acabou de ser ocupado. Escolha outro, por favor."
            )
        await sessao.refresh(compromisso)
        compromisso_id = compromisso.id
        conexao = await _conexao_ativa(sessao, tenant_id)

    # Espelha no provedor externo. Falhar aqui NÃO desfaz o compromisso.
    sincronizado = False
    event_id = None
    if conexao is not None and ADAPTADORES.get(conexao.provider) is not None:
        adaptador = ADAPTADORES[conexao.provider]
        try:
            evento = await adaptador.create_event(
                tenant_id=tenant_id,
                start=inicio,
                end=fim,
                # "Cliente" em vez do telefone cru: o titulo do evento e o que
                # aparece na agenda do dono do negocio, e numero solto ali nao
                # ajuda ninguem. O telefone segue na descricao.
                summary=f"{servico.name} — {(customer_name or '').strip() or 'Cliente'}",
                description=f"Agendado pelo ATENDIT via {source}. Contato: {customer_phone}",
            )
            event_id = evento.get("event_id")
            sincronizado = True
            async with AsyncSessionLocal() as sessao:
                obj = await sessao.get(Appointment, compromisso_id)
                obj.external_event_id = event_id
                obj.provider = conexao.provider
                await sessao.commit()
        except Exception as exc:
            # provider continua 'local' e external_event_id nulo: o estado no
            # banco fica HONESTO sobre não estar no Google.
            logger.error(
                f"[SCHED] Compromisso {compromisso_id} criado localmente, mas falhou no "
                f"'{conexao.provider}': {exc}. Marcado como não sincronizado."
            )

    logger.info(
        f"[SCHED BOOK] {compromisso_id} {inicio.astimezone(FUSO_PADRAO):%d/%m %H:%M} "
        f"tenant={tenant_id} sincronizado={sincronizado}"
    )
    return {
        "appointment_id": str(compromisso_id),
        "servico": servico.name,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "rotulo": inicio.astimezone(FUSO_PADRAO).strftime("%d/%m/%Y às %H:%M"),
        "status": "confirmed",
        "sincronizado_google": sincronizado,
        "external_event_id": event_id,
    }


# --------------------------------------------------------------------------
# Cancelamento
# --------------------------------------------------------------------------
async def cancel_appointment(
    tenant_id: uuid.UUID,
    customer_phone: str,
    inicio: Optional[datetime] = None,
    appointment_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    async with AsyncSessionLocal() as sessao:
        filtros = [
            Appointment.tenant_id == tenant_id,
            Appointment.status == "confirmed",
        ]
        if appointment_id:
            filtros.append(Appointment.id == appointment_id)
        else:
            filtros.append(Appointment.customer_phone == customer_phone)
            if inicio:
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=FUSO_PADRAO)
                filtros.append(Appointment.start_at == inicio)

        alvo = (
            await sessao.execute(select(Appointment).where(and_(*filtros)).order_by(Appointment.start_at.asc()))
        ).scalars().first()

        if alvo is None:
            raise CompromissoNaoEncontrado("Não encontrei compromisso confirmado com esses dados.")

        dados = {
            "appointment_id": str(alvo.id),
            "service_type_id": alvo.service_type_id,
            "inicio": alvo.start_at,
            "fim": alvo.end_at,
            "external_event_id": alvo.external_event_id,
            "provider": alvo.provider,
        }
        alvo.status = "cancelled"
        await sessao.commit()

        conexao = await _conexao_ativa(sessao, tenant_id)

    removido_no_provedor = False
    if dados["external_event_id"] and conexao is not None:
        adaptador = ADAPTADORES.get(conexao.provider)
        if adaptador is not None:
            try:
                removido_no_provedor = await adaptador.cancel_event(
                    tenant_id=tenant_id, event_id=dados["external_event_id"]
                )
            except Exception as exc:
                logger.error(
                    f"[SCHED] Compromisso {dados['appointment_id']} cancelado localmente, mas o "
                    f"evento {dados['external_event_id']} continua no '{conexao.provider}': {exc}"
                )

    candidatos_espera = await _acionar_lista_espera(
        tenant_id=tenant_id,
        service_type_id=dados["service_type_id"],
        inicio=dados["inicio"],
        fim=dados["fim"],
    )

    logger.info(
        f"[SCHED CANCEL] {dados['appointment_id']} cancelado. "
        f"provedor_removido={removido_no_provedor} espera={candidatos_espera}"
    )
    return {
        "appointment_id": dados["appointment_id"],
        "status": "cancelled",
        "rotulo": dados["inicio"].astimezone(FUSO_PADRAO).strftime("%d/%m/%Y às %H:%M"),
        "removido_no_provedor": removido_no_provedor,
        "candidatos_lista_espera": candidatos_espera,
    }


async def reschedule_appointment(
    tenant_id: uuid.UUID,
    customer_phone: str,
    datetime_atual: Optional[datetime],
    datetime_novo: datetime,
) -> Dict[str, Any]:
    """
    Remarca um compromisso.

    ORDEM DELIBERADA: reserva o horario NOVO primeiro; so depois cancela o
    ANTIGO. O inverso -- cancelar e depois tentar reservar -- deixaria o
    cliente SEM NADA se o horario novo nao estivesse livre, e o antigo ja
    teria sido dado a outra pessoa pela lista de espera. Aqui, se o novo
    falhar, o antigo permanece intacto e o cliente so ouve "esse horario nao
    esta disponivel".

    Efeito colateral aceito: por um instante existem DOIS compromissos do
    mesmo cliente. E preferivel a janela em que ele nao tem nenhum.
    """
    if datetime_novo.tzinfo is None:
        datetime_novo = datetime_novo.replace(tzinfo=FUSO_PADRAO)
    if datetime_atual is not None and datetime_atual.tzinfo is None:
        datetime_atual = datetime_atual.replace(tzinfo=FUSO_PADRAO)

    # 1) Localiza o compromisso a remarcar ANTES de mexer em nada.
    async with AsyncSessionLocal() as sessao:
        filtros = [
            Appointment.tenant_id == tenant_id,
            Appointment.customer_phone == customer_phone,
            Appointment.status == "confirmed",
        ]
        if datetime_atual is not None:
            filtros.append(Appointment.start_at == datetime_atual)
        atual = (
            await sessao.execute(
                select(Appointment).where(and_(*filtros)).order_by(Appointment.start_at.asc())
            )
        ).scalars().first()
        if atual is None:
            raise CompromissoNaoEncontrado(
                "Não encontrei um compromisso confirmado seu para remarcar."
            )
        dados_atual = {
            "id": atual.id,
            "inicio": atual.start_at,
            "service_type_id": atual.service_type_id,
            "nome": atual.customer_name,
        }

    if dados_atual["inicio"] == datetime_novo:
        raise HorarioIndisponivel("O horário novo é o mesmo do atual.")

    # 2) RESERVA O NOVO. Se falhar, nada foi perdido.
    novo = await book_appointment(
        tenant_id=tenant_id,
        service_type_id=dados_atual["service_type_id"],
        inicio=datetime_novo,
        customer_phone=customer_phone,
        customer_name=dados_atual["nome"],
        source="whatsapp",
    )

    # 3) So agora cancela o antigo. Se ISTO falhar, o cliente tem dois
    #    horarios -- ruim, mas visivel e corrigivel; nao tem zero.
    try:
        cancelado = await cancel_appointment(
            tenant_id=tenant_id,
            customer_phone=customer_phone,
            appointment_id=dados_atual["id"],
        )
    except Exception as exc:
        logger.error(
            f"[SCHED RESCHEDULE] Novo horário {novo['appointment_id']} criado, mas o antigo "
            f"{dados_atual['id']} NÃO foi cancelado: {exc}. Cliente com dois compromissos."
        )
        return {
            "remarcado": True,
            "aviso": "o horário anterior pode não ter sido liberado",
            "de": dados_atual["inicio"].astimezone(FUSO_PADRAO).strftime("%d/%m/%Y às %H:%M"),
            "para": novo["rotulo"],
            "novo": novo,
        }

    logger.info(
        f"[SCHED RESCHEDULE] {dados_atual['id']} -> {novo['appointment_id']} "
        f"({dados_atual['inicio'].astimezone(FUSO_PADRAO):%d/%m %H:%M} -> {novo['rotulo']})"
    )
    return {
        "remarcado": True,
        "de": dados_atual["inicio"].astimezone(FUSO_PADRAO).strftime("%d/%m/%Y às %H:%M"),
        "para": novo["rotulo"],
        "novo": novo,
        "antigo_removido_no_provedor": cancelado.get("removido_no_provedor"),
    }


async def _acionar_lista_espera(
    tenant_id: uuid.UUID, service_type_id: uuid.UUID, inicio: datetime, fim: datetime
) -> int:
    """
    Gatilho da lista de espera.

    ⚠️ PARCIAL: identifica e ORDENA os candidatos cuja janela desejada cobre o
    horário que vagou, mas NÃO notifica ninguém — a notificação por WhatsApp e
    o timeout de confirmação são a PARTE 5 do roadmap, ainda não construída.
    Devolve a contagem para que quem chamou saiba que há fila, em vez de
    fingir que a fila foi tratada.
    """
    async with AsyncSessionLocal() as sessao:
        candidatos = (
            await sessao.execute(
                select(Waitlist)
                .where(
                    Waitlist.tenant_id == tenant_id,
                    Waitlist.service_type_id == service_type_id,
                    Waitlist.status == "waiting",
                    Waitlist.desired_start <= inicio,
                    Waitlist.desired_end >= fim,
                )
                .order_by(Waitlist.priority.asc(), Waitlist.desired_start.asc(), Waitlist.created_at.asc())
            )
        ).scalars().all()

    if not candidatos:
        return 0

    # Publica no Celery. O envio e o timeout rodam no worker, nao aqui: o
    # cancelamento nao pode ficar preso esperando a Evolution responder.
    try:
        from app.core.celery_app import celery_app
        import asyncio as _aio
        await _aio.to_thread(
            celery_app.send_task,
            "app.core.tasks.ofertar_vaga",
            args=[str(tenant_id), str(service_type_id), inicio.isoformat(), fim.isoformat()],
        )
        logger.info(f"[SCHED WAITLIST] {len(candidatos)} candidato(s); oferta despachada ao worker.")
    except Exception as exc:
        # Fila nao notificada nao invalida o cancelamento, que ja aconteceu.
        logger.error(f"[SCHED WAITLIST] Falha ao despachar a oferta: {exc}")

    return len(candidatos)


# --------------------------------------------------------------------------
# Consulta e fila
# --------------------------------------------------------------------------
async def list_appointments(
    tenant_id: uuid.UUID, customer_phone: str, incluir_passados: bool = False
) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as sessao:
        filtros = [Appointment.tenant_id == tenant_id, Appointment.customer_phone == customer_phone]
        if not incluir_passados:
            filtros.append(Appointment.start_at >= datetime.now(FUSO_PADRAO))
        linhas = (
            await sessao.execute(select(Appointment).where(and_(*filtros)).order_by(Appointment.start_at.asc()))
        ).scalars().all()

        nomes = {}
        for a in linhas:
            if a.service_type_id not in nomes:
                s = await sessao.get(ServiceType, a.service_type_id)
                nomes[a.service_type_id] = s.name if s else "Serviço"

    return [
        {
            "appointment_id": str(a.id),
            "servico": nomes.get(a.service_type_id, "Serviço"),
            "rotulo": a.start_at.astimezone(FUSO_PADRAO).strftime("%d/%m/%Y às %H:%M"),
            "inicio": a.start_at.isoformat(),
            "status": a.status,
        }
        for a in linhas
    ]


async def join_waitlist(
    tenant_id: uuid.UUID,
    service_type_id: Optional[uuid.UUID],
    customer_phone: str,
    desired_start: datetime,
    desired_end: datetime,
    customer_name: Optional[str] = None,
    priority: int = 100,
) -> Dict[str, Any]:
    if desired_start.tzinfo is None:
        desired_start = desired_start.replace(tzinfo=FUSO_PADRAO)
    if desired_end.tzinfo is None:
        desired_end = desired_end.replace(tzinfo=FUSO_PADRAO)
    if desired_end <= desired_start:
        raise ValueError("A janela desejada precisa terminar depois de começar.")

    async with AsyncSessionLocal() as sessao:
        servico = await _carregar_servico(sessao, tenant_id, service_type_id=service_type_id)
        entrada = Waitlist(
            tenant_id=tenant_id,
            service_type_id=servico.id,
            customer_phone=customer_phone,
            customer_name=customer_name,
            desired_start=desired_start,
            desired_end=desired_end,
            priority=priority,
            status="waiting",
        )
        sessao.add(entrada)
        await sessao.commit()
        await sessao.refresh(entrada)
        entrada_id = entrada.id

    logger.info(f"[SCHED WAITLIST] {entrada_id} entrou na fila ({customer_phone}).")
    return {
        "waitlist_id": str(entrada_id),
        "servico": servico.name,
        "de": desired_start.astimezone(FUSO_PADRAO).strftime("%d/%m %H:%M"),
        "ate": desired_end.astimezone(FUSO_PADRAO).strftime("%d/%m %H:%M"),
        "status": "waiting",
    }
