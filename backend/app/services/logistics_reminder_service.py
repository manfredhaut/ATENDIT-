"""
Serviço de Mitigação Ativa de Absenteísmo e No-Show Logístico.

Varre compromissos vinculados a ordens de serviço logísticas e despacha:
1. Lembrete de 24 Horas (Confirmação ativa / Remarcação)
2. Alerta de 2 Horas (Técnico a caminho com endereço de destino)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:
    FUSO = timezone(timedelta(hours=-3))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.logistics import LogisticConfig, ServiceOrder
from app.models.scheduling import Appointment
from app.models.tenant import Tenant
from app.services.evolution_service import evolution_service

logger = logging.getLogger("atendit.logistics_reminder")


def _resolver_instancia(tenant: Tenant) -> str:
    """Resolve o nome da instância da Evolution API do inquilino."""
    if tenant.evolution_instance:
        return tenant.evolution_instance
    return f"atendit_{(tenant.slug or '').replace('-', '_')}"


async def verificar_e_enviar_lembretes() -> Dict[str, Any]:
    """Rotina periódica executada via Celery Beat."""
    agora = datetime.now(timezone.utc)
    enviados_24h = 0
    enviados_2h = 0
    falhas = 0

    async with AsyncSessionLocal() as sessao:
        # Busca ordens com agendamentos futuros confirmados
        registros = (
            await sessao.execute(
                select(ServiceOrder, Appointment, LogisticConfig, Tenant)
                .join(Appointment, Appointment.id == ServiceOrder.appointment_id)
                .join(LogisticConfig, LogisticConfig.tenant_id == ServiceOrder.tenant_id)
                .join(Tenant, Tenant.id == ServiceOrder.tenant_id)
                .where(
                    ServiceOrder.status == "scheduled",
                    Appointment.status == "confirmed",
                    Appointment.start_at > agora,
                )
            )
        ).all()

        if not registros:
            return {"verificados": 0, "enviados_24h": 0, "enviados_2h": 0, "falhas": 0}

        for ordem, appt, config, tenant in registros:
            instancia = _resolver_instancia(tenant)
            telefone = ordem.customer_phone or appt.customer_phone
            nome_cliente = appt.customer_name or "Cliente"
            inicio_fmt = appt.start_at.astimezone(FUSO).strftime("%d/%m/%Y às %H:%M")
            hora_fmt = appt.start_at.astimezone(FUSO).strftime("%H:%M")

            # --- Régua 24 Horas ---
            limite_24h = appt.start_at - timedelta(hours=24)
            if (
                config.remind_24h
                and not ordem.confirmation_24h_sent
                and agora >= limite_24h
                and agora < (appt.start_at - timedelta(hours=2))
            ):
                texto_24h = (
                    f"Olá, {nome_cliente}! Lembramos da sua visita técnica / atendimento agendado para amanhã, "
                    f"*{inicio_fmt}*, no endereço:\n📍 {ordem.destination_address}\n\n"
                    f"Por favor, responda com:\n"
                    f"1️⃣ para *Confirmar Presença*\n"
                    f"2️⃣ para *Remarcar Horário*"
                )
                try:
                    ok = await evolution_service.send_text_message(
                        instance_name=instancia,
                        recipient_number=telefone,
                        text=texto_24h,
                    )
                    if ok:
                        ordem.confirmation_24h_sent = True
                        enviados_24h += 1
                        logger.info(f"[LOGISTICA 24H] Lembrete enviado para {telefone} (OS {ordem.id})")
                    else:
                        falhas += 1
                except Exception as exc:
                    falhas += 1
                    logger.error(f"[LOGISTICA 24H] Erro ao enviar para {telefone}: {exc}")

            # --- Régua 2 Horas ---
            limite_2h = appt.start_at - timedelta(hours=2)
            if (
                config.remind_2h
                and not ordem.confirmation_2h_sent
                and agora >= limite_2h
                and agora < appt.start_at
            ):
                texto_2h = (
                    f"Olá, {nome_cliente}! Sua visita técnica está confirmada para hoje às *{hora_fmt}* "
                    f"no endereço:\n📍 {ordem.destination_address}\n\n"
                    f"🚗 Nossa equipe técnica já está organizando a rota de atendimento. Até breve!"
                )
                try:
                    ok = await evolution_service.send_text_message(
                        instance_name=instancia,
                        recipient_number=telefone,
                        text=texto_2h,
                    )
                    if ok:
                        ordem.confirmation_2h_sent = True
                        enviados_2h += 1
                        logger.info(f"[LOGISTICA 2H] Alerta de rota enviado para {telefone} (OS {ordem.id})")
                    else:
                        falhas += 1
                except Exception as exc:
                    falhas += 1
                    logger.error(f"[LOGISTICA 2H] Erro ao enviar para {telefone}: {exc}")

        await sessao.commit()

    return {
        "verificados": len(registros),
        "enviados_24h": enviados_24h,
        "enviados_2h": enviados_2h,
        "falhas": falhas,
    }
