"""
Lembretes automáticos de compromisso.

A task roda a cada 5 minutos e envia o lembrete quando o compromisso entra
na janela de antecedência configurada no service_type
(reminder_offset_minutes e, opcionalmente, reminder_offset_minutes_2).

--------------------------------------------------------------------------
IDEMPOTÊNCIA — a parte que importa
--------------------------------------------------------------------------
Rodando a cada 5 min, a MESMA consulta reencontra o mesmo compromisso
dezenas de vezes dentro da janela. O que impede o cliente de receber doze
lembretes por hora é a UNIQUE (appointment_id, reminder_type,
offset_minutes) do reminder_log.

A ordem é deliberada: RESERVA a linha primeiro (status='pending'), depois
envia, depois marca 'sent'.

  - Se enviássemos antes de gravar, uma falha entre o envio e o INSERT
    reenviaria na próxima rodada — cliente recebe duas vezes.
  - Reservando antes, uma segunda execução concorrente colide na UNIQUE e
    desiste, porque a linha já existe.

O preço é o caso raro em que o INSERT passa e o envio falha: fica
'failed' e NÃO é reenviado automaticamente. Preferi lembrete perdido a
lembrete duplicado — o cliente que não recebe pode ser avisado por outro
caminho; o que recebe quatro vezes perde a confiança no sistema.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.scheduling import Appointment, ReminderLog, ServiceType
from app.models.tenant import Tenant
from app.services.evolution_service import evolution_service

logger = logging.getLogger("atendit.reminders")

FUSO = __import__("zoneinfo").ZoneInfo("America/Sao_Paulo")

# Tolerância da janela. A task roda a cada 5 min; sem folga, um compromisso
# cujo instante exato de disparo caísse entre duas execuções nunca seria
# pego. 10 min cobre um atraso eventual do worker sem antecipar demais.
TOLERANCIA_MINUTOS = 10


def _nome_instancia(slug: str) -> str:
    """Mesma convenção de ensure_evolution_instance() no main.py."""
    return f"atendit_{(slug or '').replace('-', '_')}"


async def _texto_lembrete(
    tenant_id: uuid.UUID, nome: Optional[str], servico: str, inicio: datetime, offset_minutes: Optional[int] = None
) -> str:
    """Texto do lembrete, diferenciando D-0 (reforço) de D-1 (confirmação ativa)."""
    from app.services import template_service

    # Se for D-0 (antecedência curta <= 180 min, ex: 2 horas antes)
    if offset_minutes and offset_minutes <= 180:
        horario_str = inicio.astimezone(FUSO).strftime("%H:%M")
        return (
            f"Olá, {(nome or '').strip() or 'Cliente'}! Passando para lembrar que seu atendimento de "
            f"*{servico}* é HOJE às *{horario_str}*. Estamos te aguardando!"
        )

    # D-1 (antecedência longa >= 720 min, ex: 24h antes) - Chamada de confirmação ativa
    return await template_service.render(
        tenant_id=tenant_id,
        template_type="reminder",
        nome=nome,
        servico=servico,
        horario=inicio.astimezone(FUSO).strftime("%d/%m às %H:%M"),
    )


async def _pendentes(agora: datetime) -> List[Dict[str, Any]]:
    """
    Compromissos que entraram na janela de lembrete e ainda não têm registro.

    Uma linha por (compromisso, offset): o mesmo compromisso pode ter dois
    lembretes configurados, e cada um é reservado separadamente.
    """
    saida: List[Dict[str, Any]] = []

    async with AsyncSessionLocal() as sessao:
        linhas = (
            await sessao.execute(
                select(Appointment, ServiceType, Tenant)
                .join(ServiceType, ServiceType.id == Appointment.service_type_id)
                .join(Tenant, Tenant.id == Appointment.tenant_id)
                .where(
                    Appointment.status == "confirmed",
                    Appointment.start_at > agora,
                )
            )
        ).all()

        for compromisso, servico, tenant in linhas:
            offsets = [servico.reminder_offset_minutes]
            if servico.reminder_offset_minutes_2:
                offsets.append(servico.reminder_offset_minutes_2)

            for offset in offsets:
                if not offset or offset <= 0:
                    continue
                disparo = compromisso.start_at - timedelta(minutes=offset)
                # Dentro da janela: já passou do instante de disparo, mas não
                # tanto que o lembrete tenha perdido o sentido.
                if not (disparo <= agora <= disparo + timedelta(minutes=TOLERANCIA_MINUTOS)):
                    continue

                ja_existe = (
                    await sessao.execute(
                        select(ReminderLog).where(
                            and_(
                                ReminderLog.appointment_id == compromisso.id,
                                ReminderLog.reminder_type == "reminder",
                                ReminderLog.offset_minutes == offset,
                            )
                        )
                    )
                ).scalar_one_or_none()
                if ja_existe:
                    continue

                saida.append(
                    {
                        "appointment_id": compromisso.id,
                        "tenant_id": compromisso.tenant_id,
                        "telefone": compromisso.customer_phone,
                        "nome": compromisso.customer_name,
                        "servico": servico.name,
                        "inicio": compromisso.start_at,
                        "offset": offset,
                        "instancia": _nome_instancia(tenant.slug),
                        "disparo_previsto": disparo,
                    }
                )

    return saida


async def enviar_lembretes_pendentes() -> Dict[str, Any]:
    agora = datetime.now(timezone.utc)
    pendentes = await _pendentes(agora)

    if not pendentes:
        return {"verificados": 0, "enviados": 0, "falhados": 0, "ja_registrados": 0}

    logger.info(f"[LEMBRETE] {len(pendentes)} lembrete(s) na janela.")
    enviados = falhados = colididos = 0

    for item in pendentes:
        # 1) RESERVA a linha. Se colidir, outro processo já cuidou deste.
        async with AsyncSessionLocal() as sessao:
            registro = ReminderLog(
                appointment_id=item["appointment_id"],
                reminder_type="reminder",
                offset_minutes=item["offset"],
                scheduled_at=item["disparo_previsto"],
                status="pending",
            )
            sessao.add(registro)
            try:
                await sessao.commit()
            except IntegrityError:
                await sessao.rollback()
                colididos += 1
                logger.info(
                    f"[LEMBRETE] {item['appointment_id']} offset={item['offset']} "
                    f"já registrado; nada a enviar."
                )
                continue
            registro_id = registro.id

        # 2) Envia.
        texto = await _texto_lembrete(
            item["tenant_id"], item["nome"], item["servico"], item["inicio"], offset_minutes=item.get("offset")
        )
        ok = False
        try:
            ok = await evolution_service.send_text_message(
                instance_name=item["instancia"],
                recipient_number=item["telefone"],
                text=texto,
            )
        except Exception as exc:
            logger.error(f"[LEMBRETE] Falha ao enviar para {item['telefone']}: {exc}")

        # 3) Fecha o registro com o resultado real.
        async with AsyncSessionLocal() as sessao:
            reg = await sessao.get(ReminderLog, registro_id)
            if reg:
                reg.status = "sent" if ok else "failed"
                reg.sent_at = datetime.now(timezone.utc) if ok else None
                await sessao.commit()

        if ok:
            enviados += 1
            logger.info(
                f"[LEMBRETE ENVIADO] {item['appointment_id']} -> {item['telefone']} "
                f"({item['offset']}min antes de {item['inicio'].astimezone(FUSO):%d/%m %H:%M})"
            )
        else:
            falhados += 1
            logger.error(
                f"[LEMBRETE FALHOU] {item['appointment_id']} -> {item['telefone']}. "
                f"Registrado como 'failed'; NÃO será reenviado automaticamente."
            )

    return {
        "verificados": len(pendentes),
        "enviados": enviados,
        "falhados": falhados,
        "ja_registrados": colididos,
    }
