"""
Autenticação das rotas administrativas.

Contexto: até esta mudança, TODA rota de escrita da API estava aberta na
internet — criar inquilino, gravar chave de API de IA, subir documento para
o RAG e parear número de WhatsApp podiam ser feitos por qualquer um.

Esta é uma trava de máquina-a-máquina (o painel e scripts internos), não um
sistema de login de usuário. Não substitui autenticação de sessão: quando
houver login real, as rotas passam a exigir sessão E este token, não um ou
outro.

NAO se aplica ao webhook da Evolution: quem chama aquela rota e o gateway
externo, que nao tem como enviar este header.

ATENCAO (corrigido em 2026-09-01): este comentario afirmava que o webhook
"tem o mecanismo proprio (WEBHOOK_SECRET)". ERA FALSO, e a afirmacao foi
copiada daqui para o RESTAURAR-AUTH.md. Nao existe implementacao de
WEBHOOK_SECRET em lugar nenhum -- o nome so aparecia neste comentario. A
rota POST /webhook/evolution/{tenant_id} responde 200 a qualquer POST, sem
autenticacao, pelo caminho publico. Esta aberto em backend/PENDENCIAS.md.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger("atendit.security")

NOME_HEADER = "X-Internal-Token"


async def exigir_token_interno(
    x_internal_token: str = Header(
        default="",
        alias=NOME_HEADER,
        description="Token de serviço para rotas administrativas.",
    ),
) -> None:
    """
    Recusa a requisição se o header X-Internal-Token não bater com
    INTERNAL_API_TOKEN.

    Falha FECHADO: se a variável não estiver configurada, a rota é recusada
    com 503 em vez de liberada. O modo de falha oposto — token vazio
    comparando igual a header vazio e deixando passar — transformaria um
    erro de configuração em porta aberta, exatamente o que este módulo
    existe para impedir.
    """
    esperado = settings.INTERNAL_API_TOKEN or ""

    if not esperado:
        logger.error(
            "[SECURITY] INTERNAL_API_TOKEN não configurado; recusando rota administrativa."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação interna indisponível: token de serviço não configurado.",
        )

    recebido = x_internal_token or ""

    # compare_digest: comparação em tempo constante, para o tempo de resposta
    # não revelar quantos caracteres iniciais do token estão certos.
    if not hmac.compare_digest(recebido, esperado):
        if not recebido:
            logger.warning(f"[SECURITY] Requisição sem o header {NOME_HEADER} recusada.")
        else:
            logger.warning(f"[SECURITY] Requisição com {NOME_HEADER} inválido recusada.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Header {NOME_HEADER} ausente ou inválido.",
            headers={"WWW-Authenticate": NOME_HEADER},
        )
