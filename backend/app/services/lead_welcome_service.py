import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Lead
from app.models.tenant import Tenant
from app.services.evolution_service import evolution_service
from app.services import template_service

logger = logging.getLogger("atendit.lead_welcome")

def sanitizar_numero_whatsapp(numero: str) -> Optional[str]:
    """
    Sanitiza e garante formato E.164.
    Para números brasileiros (10 ou 11 dígitos), prefixa o DDI 55 caso ausente.
    """
    if not numero:
        return None
    digitos = "".join([c for c in str(numero) if c.isdigit()])
    if not digitos or len(digitos) < 8:
        return None

    # Se informado com 10 ou 11 dígitos (DDD + número), adiciona o DDI 55
    if len(digitos) in (10, 11):
        digitos = f"55{digitos}"

    return digitos

async def enviar_boas_vindas_lead(lead_id: uuid.UUID) -> bool:
    """
    Dispara mensagem de acolhimento personalizada via WhatsApp para o lead recém-capturado.
    Roda em BackgroundTasks para não impactar o tempo de resposta da API pública.
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Carregar lead
            res_l = await session.execute(select(Lead).where(Lead.id == lead_id))
            lead = res_l.scalar_one_or_none()
            if not lead:
                logger.warning(f"[BOAS-VINDAS] Lead {lead_id} não encontrado.")
                return False

            if lead.boas_vindas_enviada:
                logger.info(f"[BOAS-VINDAS] Lead {lead_id} já recebeu boas-vindas anteriormente.")
                return True

            if not lead.whatsapp:
                logger.info(f"[BOAS-VINDAS] Lead {lead_id} não possui telefone WhatsApp cadastrado.")
                return False

            # 2. Identificar inquilino e instância
            instancia_nome = None
            nome_empresa = "Nossa Equipe"
            
            if lead.tenant_id:
                res_t = await session.execute(select(Tenant).where(Tenant.id == lead.tenant_id))
                tenant = res_t.scalar_one_or_none()
                if tenant:
                    instancia_nome = tenant.evolution_instance
                    nome_empresa = tenant.name or nome_empresa

            if not instancia_nome:
                logger.warning(f"[BOAS-VINDAS] Inquilino do Lead {lead_id} não possui instância de WhatsApp ativa.")
                return False

            numero_destino = sanitizar_numero_whatsapp(lead.whatsapp)
            if not numero_destino:
                logger.warning(f"[BOAS-VINDAS] Telefone inválido para envio do Lead {lead_id}: {lead.whatsapp}")
                return False

            # 3. Montar mensagem a partir do template do inquilino
            texto_base = await template_service.obter(lead.tenant_id, "lead_welcome") if lead.tenant_id else template_service.DEFAULTS["lead_welcome"]
            
            primeiro_nome = lead.nome.strip().split()[0] if lead.nome else "Olá"
            variaveis = {
                "nome": lead.nome,
                "primeiro_nome": primeiro_nome,
                "empresa": nome_empresa,
                "whatsapp": lead.whatsapp or ""
            }
            texto_formatado = template_service._substituir(texto_base, variaveis)

            # 4. Enviar mensagem via Evolution API
            logger.info(f"[BOAS-VINDAS] Disparando WhatsApp para Lead {lead_id} ({numero_destino}) via instância '{instancia_nome}'...")
            sucesso = await evolution_service.send_text_message(
                instance_name=instancia_nome,
                recipient_number=numero_destino,
                text=texto_formatado
            )

            if sucesso:
                lead.boas_vindas_enviada = True
                lead.boas_vindas_enviada_em = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"[BOAS-VINDAS] Mensagem de boas-vindas entregue com sucesso para o Lead {lead_id}!")
                return True
            else:
                logger.warning(f"[BOAS-VINDAS] Gateway recusou o envio para o Lead {lead_id}.")
                return False

    except Exception as exc:
        logger.error(f"[BOAS-VINDAS] Falha no processamento de boas-vindas para o Lead {lead_id}: {exc}", exc_info=True)
        return False
