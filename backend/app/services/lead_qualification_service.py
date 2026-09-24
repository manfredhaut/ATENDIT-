import json
import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Lead
from app.models.tenant import Tenant
from app.services.llm_service import llm_service

logger = logging.getLogger("atendit.lead_qualification")

SYSTEM_PROMPT_QUALIFICACAO = """Você é um especialista em qualificação comercial e triagem de leads (SDR inteligente).
Sua função é analisar os dados de um novo lead recebido (nome, comentário, origem, campanhas) e classificar seu potencial comercial de forma objetiva.

Critérios de Classificação:
1. Temperatura:
   - "quente": Demonstra urgência clara, intenção explícita de compra/contratação, orçamento ou necessidade imediata.
   - "morno": Interesse genuíno, pedido de cotação ou dúvidas técnicas detalhadas.
   - "frio": Apenas curiosidade vaga, dados incompletos ou mensagem genérica sem intenção clara.

2. Score (0 a 100):
   - 80 a 100: Altíssima propensão (quente).
   - 40 a 79: Propensão média (morno).
   - 0 a 39: Baixa propensão (frio).

3. Prioridade:
   - "alta": Leads quentes que exigem contato em menos de 10 minutos.
   - "media": Leads mornos com atendimento no fluxo normal.
   - "baixa": Leads frios ou dados parciais.

Sua resposta DEVE ser EXCLUSIVAMENTE um objeto JSON válido no seguinte formato, sem nenhum texto antes ou depois:
{
  "temperatura": "quente" | "morno" | "frio",
  "score": 85,
  "intencao": "síntese da necessidade em até 6 palavras",
  "prioridade": "alta" | "media" | "baixa",
  "resumo_ia": "análise concisa de 1 a 2 frases justificando a nota e recomendando o próximo passo comercial"
}
"""

async def qualificar_lead_ia(lead_id: uuid.UUID) -> Optional[dict]:
    """
    Executa a qualificação do lead via LLM em segundo plano e persiste o resultado.
    """
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Lead).where(Lead.id == lead_id))
            lead = res.scalar_one_or_none()
            if not lead:
                logger.warning(f"[QUALIFICADOR IA] Lead {lead_id} não encontrado.")
                return None

            # Obter nome da empresa / segmento se vinculado a um tenant
            nome_empresa = "Empresa"
            if lead.tenant_id:
                res_t = await session.execute(select(Tenant.name).where(Tenant.id == lead.tenant_id))
                t_nome = res_t.scalar_one_or_none()
                if t_nome:
                    nome_empresa = t_nome

            mensagem_usuario = f"""Avalie este novo contato recebido para a empresa '{nome_empresa}':
- Nome: {lead.nome}
- Telefone/WhatsApp: {lead.whatsapp or 'Não informado'}
- E-mail: {lead.email or 'Não informado'}
- Mensagem/Comentário: {lead.comentario or 'Sem comentário preenchido'}
- Origem: {lead.origem}
- Campanha (UTM): {lead.utm_campaign or 'Orgânico'} (Source: {lead.utm_source or 'Direto'})
"""

            logger.info(f"[QUALIFICADOR IA] Iniciando análise do Lead {lead_id}...")
            resposta_llm = await llm_service.generate_response(
                system_prompt=SYSTEM_PROMPT_QUALIFICACAO,
                user_message=mensagem_usuario,
                model='gemini-flash-latest',
                temperature=0.2
            )

            # Extração segura de JSON da resposta
            dados = None
            try:
                # Remove potenciais blocos markdown ```json ... ```
                json_str = re.sub(r"^```json\s*", "", resposta_llm.strip())
                json_str = re.sub(r"\s*```$", "", json_str).strip()
                dados = json.loads(json_str)
            except Exception as parse_err:
                logger.warning(f"[QUALIFICADOR IA] Falha no parse JSON direto: {parse_err}. Tentando regex...")
                match = re.search(r"\{.*\}", resposta_llm, re.DOTALL)
                if match:
                    dados = json.loads(match.group(0))

            if not dados or not isinstance(dados, dict):
                logger.error(f"[QUALIFICADOR IA] Não foi possível extrair JSON válido do Lead {lead_id}: {resposta_llm}")
                return None

            # Validação e sanitização dos campos
            temperatura = str(dados.get("temperatura", "morno")).lower()
            if temperatura not in ("quente", "morno", "frio"):
                temperatura = "morno"

            score = int(dados.get("score", 50))
            score = max(0, min(100, score))

            prioridade = str(dados.get("prioridade", "media")).lower()
            if prioridade not in ("alta", "media", "baixa"):
                prioridade = "media"

            intencao = str(dados.get("intencao", "Interesse geral"))[:100]
            resumo_ia = str(dados.get("resumo_ia", "Lead qualificado automaticamente."))

            # Persistência
            lead.temperatura = temperatura
            lead.score = score
            lead.prioridade = prioridade
            lead.intencao = intencao
            lead.resumo_ia = resumo_ia
            
            # Se for qualificado com alto score, avança o status
            if temperatura == "quente" and lead.status == "novo":
                lead.status = "qualificado"

            await session.commit()
            logger.info(f"[QUALIFICADOR IA] Lead {lead_id} qualificado com sucesso! Temp: {temperatura}, Score: {score}, Prioridade: {prioridade}")
            return dados

    except Exception as exc:
        logger.error(f"[QUALIFICADOR IA] Erro ao qualificar Lead {lead_id}: {exc}", exc_info=True)
        return None
