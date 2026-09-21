"""
Réguas de mensagem — textos por inquilino, com default no código.

Um registro por (tenant_id, template_type). Se o inquilino não customizou,
usa o DEFAULT daqui: o produto precisa funcionar antes de alguém abrir a
tela de configuração, e um texto vazio no WhatsApp é pior que um texto
genérico.

Placeholders suportados: {nome}, {servico}, {horario}.
A substituição é TOLERANTE a placeholder desconhecido — se alguém salvar
"{cliente}" por engano, o texto sai com "{cliente}" literal em vez de
derrubar o envio com KeyError. Mensagem imperfeita entregue vale mais que
exceção no meio do atendimento.
"""

import logging
import re
import uuid
from typing import Dict, Optional

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.scheduling import MessageTemplate

logger = logging.getLogger("atendit.templates")

TIPOS = ("confirmation", "reminder", "waitlist_offer", "post_appointment")

DEFAULTS: Dict[str, str] = {
    "confirmation": (
        "Olá, {nome}! Seu horário de {servico} está confirmado para {horario}. "
        "Se precisar remarcar ou cancelar, é só me avisar por aqui."
    ),
    "reminder": (
        "Olá, {nome}! Passando para lembrar do seu {servico} em {horario}. "
        "Até lá!"
    ),
    "waitlist_offer": (
        "Olá, {nome}! Abriu um horário de {servico} em {horario}.\n\n"
        "Você está na nossa lista de espera e tem prioridade nele.\n"
        "Responda *SIM* para confirmar — a vaga fica reservada para você "
        "pelos próximos {minutos} minutos."
    ),
    "post_appointment": (
        "Olá, {nome}! Obrigado por comparecer ao seu {servico}. "
        "Se quiser marcar o próximo, é só me chamar."
    ),
}

_ESPACO_RESERVADO = re.compile(r"\{(\w+)\}")


def _substituir(texto: str, valores: Dict[str, str]) -> str:
    def troca(m):
        chave = m.group(1)
        return str(valores.get(chave, m.group(0)))

    return _ESPACO_RESERVADO.sub(troca, texto)


async def obter(tenant_id: uuid.UUID, template_type: str) -> str:
    """Texto salvo do inquilino, ou o default se não houver."""
    if template_type not in TIPOS:
        raise ValueError(f"template_type inválido: '{template_type}'. Válidos: {TIPOS}")

    try:
        async with AsyncSessionLocal() as sessao:
            linha = (
                await sessao.execute(
                    select(MessageTemplate).where(
                        MessageTemplate.tenant_id == tenant_id,
                        MessageTemplate.template_type == template_type,
                    )
                )
            ).scalar_one_or_none()
        if linha and (linha.body or "").strip():
            return linha.body
    except Exception as exc:
        # Banco indisponivel nao pode calar o atendimento: cai no default.
        logger.error(f"[TEMPLATE] Falha ao ler '{template_type}' de {tenant_id}: {exc}")

    return DEFAULTS[template_type]


async def render(
    tenant_id: uuid.UUID,
    template_type: str,
    nome: Optional[str] = None,
    servico: Optional[str] = None,
    horario: Optional[str] = None,
    **extras,
) -> str:
    """Busca o template e substitui os placeholders."""
    texto = await obter(tenant_id, template_type)
    valores = {
        # "Cliente" em vez de telefone cru ou vazio: o texto vai para uma
        # pessoa, e "Olá, !" e pior que "Olá, Cliente!".
        "nome": (nome or "").strip() or "Cliente",
        "servico": (servico or "").strip() or "atendimento",
        "horario": (horario or "").strip() or "o horário combinado",
    }
    valores.update({k: str(v) for k, v in extras.items()})
    return _substituir(texto, valores)


async def listar(tenant_id: uuid.UUID) -> Dict[str, Dict[str, object]]:
    """Todos os tipos, marcando quais estão customizados."""
    salvos: Dict[str, MessageTemplate] = {}
    try:
        async with AsyncSessionLocal() as sessao:
            for linha in (
                await sessao.execute(
                    select(MessageTemplate).where(MessageTemplate.tenant_id == tenant_id)
                )
            ).scalars().all():
                salvos[linha.template_type] = linha
    except Exception as exc:
        logger.error(f"[TEMPLATE] Falha ao listar de {tenant_id}: {exc}")

    saida = {}
    for tipo in TIPOS:
        linha = salvos.get(tipo)
        customizado = bool(linha and (linha.body or "").strip())
        saida[tipo] = {
            "body": linha.body if customizado else DEFAULTS[tipo],
            "customizado": customizado,
            "default": DEFAULTS[tipo],
            "atualizado_em": linha.updated_at.isoformat() if (linha and linha.updated_at) else None,
        }
    return saida


async def salvar(tenant_id: uuid.UUID, template_type: str, body: str) -> Dict[str, object]:
    """Grava (ou limpa, se body vier vazio) o texto de um tipo."""
    if template_type not in TIPOS:
        raise ValueError(f"template_type inválido: '{template_type}'. Válidos: {TIPOS}")

    async with AsyncSessionLocal() as sessao:
        linha = (
            await sessao.execute(
                select(MessageTemplate).where(
                    MessageTemplate.tenant_id == tenant_id,
                    MessageTemplate.template_type == template_type,
                )
            )
        ).scalar_one_or_none()

        if linha is None:
            linha = MessageTemplate(tenant_id=tenant_id, template_type=template_type, body=body or "")
            sessao.add(linha)
        else:
            linha.body = body or ""
        await sessao.commit()

    customizado = bool((body or "").strip())
    logger.info(
        f"[TEMPLATE] '{template_type}' de {tenant_id} "
        f"{'salvo' if customizado else 'limpo (volta ao default)'}."
    )
    return {"template_type": template_type, "customizado": customizado}
