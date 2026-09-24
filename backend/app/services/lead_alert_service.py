import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Lead
from app.models.tenant import Tenant
from app.services.evolution_service import evolution_service
from app.services.lead_welcome_service import sanitizar_numero_whatsapp

logger = logging.getLogger("atendit.lead_alert")

async def enviar_alerta_lead_quente(lead_id: uuid.UUID) -> bool:
    """
    Envia notificação em tempo real no WhatsApp da equipe/gestor quando um lead
    quente (ou de alta prioridade) é identificado pelo motor de qualificação.
    """
    try:
        async with AsyncSessionLocal() as session:
            res_l = await session.execute(select(Lead).where(Lead.id == lead_id))
            lead = res_l.scalar_one_or_none()
            if not lead:
                logger.warning(f"[ALERTA EQUIPE] Lead {lead_id} não encontrado.")
                return False

            # Verifica critérios de urgência / alta propensão
            e_quente = (lead.temperatura == "quente") or (lead.score >= 80) or (lead.prioridade == "alta")
            if not e_quente:
                logger.info(f"[ALERTA EQUIPE] Lead {lead_id} não atinge critérios de lead quente (Temp={lead.temperatura}, Score={lead.score}). Alerta não disparado.")
                return False

            if lead.alerta_equipe_enviado:
                logger.info(f"[ALERTA EQUIPE] Alerta para Lead {lead_id} já foi enviado anteriormente.")
                return True

            if not lead.tenant_id:
                logger.warning(f"[ALERTA EQUIPE] Lead {lead_id} sem tenant vinculado.")
                return False

            res_t = await session.execute(select(Tenant).where(Tenant.id == lead.tenant_id))
            tenant = res_t.scalar_one_or_none()
            if not tenant:
                logger.warning(f"[ALERTA EQUIPE] Tenant do Lead {lead_id} não encontrado.")
                return False

            # Destino do alerta: whatsapp_notificacoes ou whatsapp_number_e164 do próprio tenant
            destinatario_bruto = tenant.whatsapp_notificacoes or tenant.whatsapp_number_e164
            if not destinatario_bruto:
                logger.warning(f"[ALERTA EQUIPE] Tenant '{tenant.name}' não possui número configurado para alertas internos.")
                return False

            destinatario = sanitizar_numero_whatsapp(destinatario_bruto)
            instancia = tenant.evolution_instance

            if not instancia:
                logger.warning(f"[ALERTA EQUIPE] Tenant '{tenant.name}' sem instância de WhatsApp ativa.")
                return False

            # Formata link para atendimento direto no WhatsApp do lead
            link_wa = ""
            if lead.whatsapp:
                num_limpo = sanitizar_numero_whatsapp(lead.whatsapp)
                if num_limpo:
                    link_wa = f"https://wa.me/{num_limpo}"

            # Montagem do template do alerta
            msg_alerta = f"""🚨 *ALERTA: NOVO LEAD QUENTE CAPTURADO!* 🚨
*Empresa:* {tenant.name}

👤 *Lead:* {lead.nome}
📱 *WhatsApp:* {lead.whatsapp or 'Não informado'}
🎯 *Intenção:* {lead.intencao or 'Interesse imediato'}
🔥 *Temperatura:* {lead.temperatura.upper()} | *Score:* {lead.score}/100
⚡ *Prioridade:* {lead.prioridade.upper()}

💬 *Mensagem/Comentário:*
"{lead.comentario or 'Sem comentário adicional'}"

📊 *Origem:* {lead.origem}
🏷️ *Campanha:* {lead.utm_campaign or 'Orgânico'} (Source: {lead.utm_source or 'Direto'})
"""
            if link_wa:
                msg_alerta += f"\n👉 *Iniciar Atendimento Imediato:*\n{link_wa}"

            logger.info(f"[ALERTA EQUIPE] Despachando alerta do Lead {lead_id} para equipe ({destinatario}) via '{instancia}'...")
            sucesso = await evolution_service.send_text_message(
                instance_name=instancia,
                recipient_number=destinatario,
                text=msg_alerta
            )

            if sucesso:
                lead.alerta_equipe_enviado = True
                lead.alerta_equipe_enviado_em = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"[ALERTA EQUIPE] Notificação entregue com sucesso para equipe sobre Lead {lead_id}!")
                return True
            else:
                logger.warning(f"[ALERTA EQUIPE] Falha no gateway ao notificar equipe sobre Lead {lead_id}.")
                return False

    except Exception as exc:
        logger.error(f"[ALERTA EQUIPE] Erro ao disparar alerta interno para Lead {lead_id}: {exc}", exc_info=True)
        return False
