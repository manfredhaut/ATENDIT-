import json
import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.scheduling import Lead
from app.models.tenant import Tenant
from app.services.llm_service import llm_service, TodosModelosIndisponiveis

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
   - "alta": Leads quentes que exigem contato imediato (<10 min).
   - "media": Leads mornos com atendimento no fluxo normal.
   - "baixa": Leads frios ou dados parciais.

Sua resposta DEVE ser EXCLUSIVAMENTE um objeto JSON válido no seguinte formato:
{
  "temperatura": "quente" | "morno" | "frio",
  "score": 85,
  "intencao": "síntese da necessidade em até 6 palavras",
  "prioridade": "alta" | "media" | "baixa",
  "resumo_ia": "análise concisa de 1 a 2 frases justificando a nota"
}
"""

def _classificacao_heuristica_resiliente(lead: Lead) -> dict:
    """Motor de regras heurísticas caso o provedor de LLM esteja sob sobrecarga ou indisponível."""
    texto = f"{lead.comentario or ''} {lead.origem or ''} {lead.utm_campaign or ''}".lower()
    
    termos_quentes = ["urgente", "fechar", "contratar", "preco", "preço", "valor", "comprar", "imediato", "fechamento", "atendimento"]
    termos_mornos = ["duvida", "dúvida", "como funciona", "integrar", "api", "orcamento", "orçamento", "informacao", "informação", "documentacao", "documentação", "erp"]
    
    score = 50
    temperatura = "morno"
    prioridade = "media"
    intencao = "Interesse geral no serviço"

    pontos_quentes = sum(1 for t in termos_quentes if t in texto)
    pontos_mornos = sum(1 for t in termos_mornos if t in texto)

    if pontos_quentes > 0:
        temperatura = "quente"
        score = min(95, 80 + (pontos_quentes * 5))
        prioridade = "alta"
        intencao = "Demanda urgente de contratação"
        resumo = "Lead com indicativo claro de urgência e contratação (classificação heurística de alta disponibilidade)."
    elif pontos_mornos > 0:
        temperatura = "morno"
        score = 65
        prioridade = "media"
        intencao = "Consulta técnica ou comercial"
        resumo = "Lead interessado em detalhes de funcionalidade e escopo técnico."
    else:
        temperatura = "frio"
        score = 35
        prioridade = "baixa"
        intencao = "Contato preliminar"
        resumo = "Lead com pouca informação descritiva fornecida."

    if lead.whatsapp:
        score = min(100, score + 5)

    return {
        "temperatura": temperatura,
        "score": score,
        "prioridade": prioridade,
        "intencao": intencao,
        "resumo_ia": resumo
    }

async def qualificar_lead_ia(lead_id: uuid.UUID) -> Optional[dict]:
    """Qualifica o lead utilizando LLM com fallback automático para regras heurísticas resilientes."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Lead).where(Lead.id == lead_id))
            lead = res.scalar_one_or_none()
            if not lead:
                logger.warning(f"[QUALIFICADOR IA] Lead {lead_id} não encontrado.")
                return None

            nome_empresa = "Empresa"
            if lead.tenant_id:
                res_t = await session.execute(select(Tenant.name).where(Tenant.id == lead.tenant_id))
                t_nome = res_t.scalar_one_or_none()
                if t_nome:
                    nome_empresa = t_nome

            mensagem_usuario = f"""Avalie este lead para '{nome_empresa}':
- Nome: {lead.nome}
- Telefone: {lead.whatsapp or 'Não informado'}
- Email: {lead.email or 'Não informado'}
- Mensagem: {lead.comentario or 'Sem comentário'}
- Origem: {lead.origem} | Campanha: {lead.utm_campaign or 'Orgânico'}
"""
            dados = None
            try:
                # Tenta modelos rápidos e resilientes
                resposta_llm = await llm_service.generate_response(
                    system_prompt=SYSTEM_PROMPT_QUALIFICACAO,
                    user_message=mensagem_usuario,
                    model="gemini-flash-lite-latest",
                    temperature=0.2
                )
                json_str = re.sub(r"^```json\s*", "", resposta_llm.strip())
                json_str = re.sub(r"\s*```$", "", json_str).strip()
                dados = json.loads(json_str)
            except Exception as llm_err:
                logger.warning(f"[QUALIFICADOR IA] LLM indisponível ({llm_err}). Acionando fallback heurístico resiliente...")
                dados = _classificacao_heuristica_resiliente(lead)

            if not dados or not isinstance(dados, dict):
                dados = _classificacao_heuristica_resiliente(lead)

            # Persistência
            lead.temperatura = str(dados.get("temperatura", "morno")).lower()
            lead.score = int(dados.get("score", 50))
            lead.prioridade = str(dados.get("prioridade", "media")).lower()
            lead.intencao = str(dados.get("intencao", "Interesse geral"))[:100]
            lead.resumo_ia = str(dados.get("resumo_ia", "Lead qualificado com sucesso."))

            if lead.temperatura == "quente" and lead.status == "novo":
                lead.status = "qualificado"

            await session.commit()
            logger.info(f"[QUALIFICADOR IA] Lead {lead_id} qualificado com sucesso! Temp: {lead.temperatura}, Score: {lead.score}")
            return dados

    except Exception as exc:
        logger.error(f"[QUALIFICADOR IA] Erro ao qualificar Lead {lead_id}: {exc}", exc_info=True)
        return None
