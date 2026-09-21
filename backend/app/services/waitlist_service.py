"""
Lista de espera: oferta, timeout e confirmação.

Ciclo:
  cancelamento -> ofertar_proximo() notifica o MELHOR candidato e marca
  'notified' -> Celery agenda expirar_e_repassar() para daqui a
  waitlist_timeout_minutes -> se ninguém confirmou, marca 'expired' e
  oferta ao próximo -> e assim por diante até a fila acabar.

DECISÃO: a oferta é EXCLUSIVA e por tempo determinado — um candidato por
vez, não "avisa todo mundo e quem chegar primeiro leva". Avisar todos
geraria corrida entre clientes e, pior, várias pessoas achando que têm o
horário. A restrição de exclusão do banco impediria a dupla marcação, mas
o estrago de expectativa já teria acontecido no WhatsApp.

O estado 'notified' + offered_start é o que permite reconhecer um "sim"
solto depois: sem gravar QUAL horário foi ofertado, a confirmação não teria
a que se referir.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select

from app.core.database import AsyncSessionLocal
from app.models.scheduling import ServiceType, Waitlist
from app.models.tenant import Tenant
from app.services.evolution_service import evolution_service

logger = logging.getLogger("atendit.waitlist")

FUSO = ZoneInfo("America/Sao_Paulo")


def _nome_instancia(slug: str) -> str:
    """Espelha ensure_evolution_instance() do main.py."""
    return f"atendit_{(slug or '').replace('-', '_')}"


async def _texto_oferta(
    tenant_id: uuid.UUID, nome: Optional[str], servico: str, inicio: datetime, minutos: int
) -> str:
    """Texto da oferta, vindo da regua do inquilino (com default no codigo)."""
    from app.services import template_service

    return await template_service.render(
        tenant_id=tenant_id,
        template_type="waitlist_offer",
        nome=nome,
        servico=servico,
        horario=inicio.astimezone(FUSO).strftime("%d/%m às %H:%M"),
        minutos=minutos,
    )


async def ofertar_proximo(
    tenant_id: uuid.UUID,
    service_type_id: uuid.UUID,
    inicio: datetime,
    fim: datetime,
    excluir_ids: Optional[list] = None,
) -> Dict[str, Any]:
    """Notifica o melhor candidato cuja janela desejada cobre o horário vago."""
    excluir_ids = excluir_ids or []

    async with AsyncSessionLocal() as sessao:
        filtros = [
            Waitlist.tenant_id == tenant_id,
            Waitlist.service_type_id == service_type_id,
            Waitlist.status == "waiting",
            Waitlist.desired_start <= inicio,
            Waitlist.desired_end >= fim,
        ]
        if excluir_ids:
            filtros.append(Waitlist.id.notin_(excluir_ids))

        candidato = (
            await sessao.execute(
                select(Waitlist)
                .where(and_(*filtros))
                .order_by(
                    Waitlist.priority.asc(),
                    Waitlist.desired_start.asc(),
                    Waitlist.created_at.asc(),
                )
            )
        ).scalars().first()

        if candidato is None:
            logger.info(f"[WAITLIST] Fila vazia para o horário {inicio.isoformat()} (tenant {tenant_id}).")
            return {"ofertado": False, "motivo": "fila_vazia"}

        servico = await sessao.get(ServiceType, service_type_id)
        tenant = await sessao.get(Tenant, tenant_id)
        minutos = servico.waitlist_timeout_minutes if servico else 15

        candidato.status = "notified"
        candidato.notified_at = datetime.now(timezone.utc)
        candidato.offered_start = inicio
        candidato.offered_end = fim
        await sessao.commit()

        dados = {
            "waitlist_id": str(candidato.id),
            "telefone": candidato.customer_phone,
            "nome": candidato.customer_name,
            "servico": servico.name if servico else "Atendimento",
            # Mesma convencao de ensure_evolution_instance() no main.py.
            # Usar tenant.slug puro mandava para "manitest" e a Evolution
            # devolvia 404 -- a instancia real e "atendit_manitest".
            "instancia": _nome_instancia(tenant.slug) if tenant else None,
        }

    texto = await _texto_oferta(tenant_id, dados["nome"], dados["servico"], inicio, minutos)
    enviado = False
    try:
        enviado = await evolution_service.send_text_message(
            instance_name=dados["instancia"],
            recipient_number=dados["telefone"],
            text=texto,
        )
    except Exception as exc:
        # A oferta CONTINUA valendo mesmo sem entrega: o registro está
        # 'notified' e o timeout vai correr. Marcar como não ofertado aqui
        # faria a fila pular um candidato por causa de falha de gateway.
        logger.error(f"[WAITLIST] Falha ao notificar {dados['telefone']}: {exc}")

    logger.info(
        f"[WAITLIST OFERTA] {dados['waitlist_id']} -> {dados['telefone']} "
        f"({inicio.astimezone(FUSO):%d/%m %H:%M}) enviado={enviado} timeout={minutos}min"
    )
    return {
        "ofertado": True,
        "waitlist_id": dados["waitlist_id"],
        "telefone": dados["telefone"],
        "enviado_whatsapp": enviado,
        "timeout_minutos": minutos,
    }


async def expirar_e_repassar(waitlist_id: uuid.UUID) -> Dict[str, Any]:
    """Se ninguém confirmou dentro do prazo, expira e oferta ao próximo."""
    async with AsyncSessionLocal() as sessao:
        entrada = await sessao.get(Waitlist, waitlist_id)
        if entrada is None:
            return {"acao": "nada", "motivo": "entrada_inexistente"}
        if entrada.status != "notified":
            # Já confirmou ('booked') ou já foi expirada por outro caminho.
            # Não é erro: é a corrida normal entre o timeout e a resposta.
            return {"acao": "nada", "motivo": f"status_atual={entrada.status}"}

        entrada.status = "expired"
        dados = {
            "tenant_id": entrada.tenant_id,
            "service_type_id": entrada.service_type_id,
            "inicio": entrada.offered_start,
            "fim": entrada.offered_end,
        }
        await sessao.commit()

    logger.info(f"[WAITLIST EXPIRA] {waitlist_id} não confirmou no prazo; repassando.")

    if not dados["inicio"] or not dados["fim"]:
        return {"acao": "expirado", "repassado": False, "motivo": "sem_horario_ofertado"}

    proximo = await ofertar_proximo(
        tenant_id=dados["tenant_id"],
        service_type_id=dados["service_type_id"],
        inicio=dados["inicio"],
        fim=dados["fim"],
        excluir_ids=[waitlist_id],
    )
    return {"acao": "expirado", "repassado": proximo.get("ofertado", False), "proximo": proximo}


async def oferta_pendente(tenant_id: uuid.UUID, telefone: str) -> Optional[Dict[str, Any]]:
    """
    Oferta ainda válida para este telefone, se houver.

    Confere o prazo em Python além do status: se a task de timeout atrasar
    (worker ocupado, fila cheia), o registro ainda estaria 'notified' e um
    "sim" tardio agendaria um horário que já foi repassado.
    """
    async with AsyncSessionLocal() as sessao:
        entrada = (
            await sessao.execute(
                select(Waitlist)
                .where(
                    Waitlist.tenant_id == tenant_id,
                    Waitlist.customer_phone == telefone,
                    Waitlist.status == "notified",
                )
                .order_by(Waitlist.notified_at.desc())
            )
        ).scalars().first()
        if entrada is None or not entrada.offered_start:
            return None

        servico = await sessao.get(ServiceType, entrada.service_type_id)
        minutos = servico.waitlist_timeout_minutes if servico else 15
        limite = (entrada.notified_at or datetime.now(timezone.utc)) + timedelta(minutes=minutos)
        if datetime.now(timezone.utc) > limite:
            return None

        return {
            "waitlist_id": entrada.id,
            "service_type_id": entrada.service_type_id,
            "offered_start": entrada.offered_start,
            "customer_name": entrada.customer_name,
            "servico": servico.name if servico else "Atendimento",
        }


async def confirmar_oferta(tenant_id: uuid.UUID, telefone: str) -> Dict[str, Any]:
    """Converte a oferta pendente em compromisso confirmado."""
    from app.services import scheduling_service as sched

    pendente = await oferta_pendente(tenant_id, telefone)
    if pendente is None:
        return {"confirmado": False, "motivo": "sem_oferta_pendente"}

    try:
        reserva = await sched.book_appointment(
            tenant_id=tenant_id,
            service_type_id=pendente["service_type_id"],
            inicio=pendente["offered_start"],
            customer_phone=telefone,
            customer_name=pendente["customer_name"],
            source="whatsapp",
        )
    except sched.HorarioIndisponivel as exc:
        # Alguém marcou o horário entre a oferta e o "sim". A entrada volta
        # para 'waiting' em vez de morrer: o cliente continua na fila.
        async with AsyncSessionLocal() as sessao:
            entrada = await sessao.get(Waitlist, pendente["waitlist_id"])
            if entrada:
                entrada.status = "waiting"
                entrada.offered_start = None
                entrada.offered_end = None
                await sessao.commit()
        return {"confirmado": False, "motivo": "horario_tomado", "detalhe": str(exc)}

    async with AsyncSessionLocal() as sessao:
        entrada = await sessao.get(Waitlist, pendente["waitlist_id"])
        if entrada:
            entrada.status = "booked"
            await sessao.commit()

    logger.info(f"[WAITLIST CONFIRMA] {pendente['waitlist_id']} virou compromisso {reserva['appointment_id']}.")
    return {"confirmado": True, "reserva": reserva, "servico": pendente["servico"]}
