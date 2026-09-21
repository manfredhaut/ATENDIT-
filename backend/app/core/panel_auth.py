"""
Autenticação do painel — sessão por cookie assinado.

DESENHO: LISTA DE PROTEÇÃO POR EXCEÇÃO
--------------------------------------------------------------------------
Só os caminhos em CAMINHOS_PROTEGIDOS exigem sessão. Todo o resto passa.

O inverso — "protege tudo, exceto esta lista de públicas" — é mais seguro
no papel e catastrófico na prática: basta esquecer /login na lista de
exceções para trancar o acesso ao próprio painel, sem caminho de volta
pela web. Esta abordagem falha para o lado de deixar algo desprotegido,
que é visível e corrigível, em vez de falhar trancando a porta.

Consequência assumida: rota nova nasce DESPROTEGIDA. Quem acrescentar rota
de painel precisa acrescentá-la aqui — está documentado em RESTAURAR-AUTH.md.

COOKIE: valor "<expiracao_unix>.<hmac>", assinado com PANEL_SESSION_SECRET.
Stateless de propósito — não há tabela de sessões, então reiniciar a API
não desloga ninguém. O preço é que logout não revoga o cookie no servidor:
ele só some do navegador, e um cookie copiado antes vale até expirar.
Trocar PANEL_SESSION_SECRET invalida todas as sessões de uma vez.
"""

import hashlib
import hmac
import logging
import time
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("atendit.panel_auth")

NOME_COOKIE = "atendit_painel"
DURACAO_SESSAO_SEGUNDOS = 12 * 60 * 60  # 12h

# Caminhos que EXIGEM sessão. Prefixo: /dashboards cobre /dashboards/qualquer.
CAMINHOS_PROTEGIDOS = (
    "/dashboards",
    "/v1/agent/config",
    "/v1/ai/copilot",
    # "/v1/tenants" cobre a LISTA, o GET por slug e o update. Ate 2026-09-01 so
    # o update estava aqui: `GET /v1/tenants/` e `GET /v1/tenants/{slug}` eram
    # PUBLICOS e devolviam nome e e-mail de todos os inquilinos a qualquer um
    # na internet. Medido antes da correcao: HTTP 200 sem cookie nenhum.
    "/v1/tenants",
    "/v1/whatsapp/qrcode",
    "/v1/whatsapp/pair-code",
    # Download de documento indexado. Tambem era publico: baixei um arquivo
    # real de outro inquilino sem credencial alguma (HTTP 200, 254 bytes).
    "/rag/export",
)


# Caminhos que exigem sessao de ADMIN especificamente -- sessao de tenant NAO
# serve. Sao telas e recursos do DONO da plataforma:
#
#   /admin, /SaaS      o painel administrativo (mesma tela, duas URLs)
#   /docs,/redoc       documentacao da API
#   /openapi.json      o esquema que a documentacao consome
#
# 🔴 Ate 2026-09-02 as SEIS eram publicas. Medido pelo dominio publico, sem
# cookie: /admin e /SaaS devolviam o HTML completo do painel administrativo, e
# /docs + /redoc + /openapi.json expunham a superficie inteira da API.
#
# O CLAUDE.md afirmava que /docs estava fechado. Aquilo descreve o codebase
# ANTIGO: nesta aplicacao o app e criado com FastAPI(title=..., version=...),
# sem docs_url=None. Nao era codigo morto -- a protecao nunca existiu aqui.
#
# ⚠️ O catch-all /{tenant_slug} tambem exige admin, mas NAO pode entrar nesta
# lista: ele casa qualquer caminho de um segmento, e um prefixo aqui casaria
# rotas que devem seguir publicas. A checagem dele vive na propria rota.
CAMINHOS_SOMENTE_ADMIN = (
    "/admin",
    "/SaaS",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def somente_admin(caminho: str) -> bool:
    c = (caminho or "/").rstrip("/") or "/"
    return any(c == p or c.startswith(p + "/") for p in CAMINHOS_SOMENTE_ADMIN)


def caminho_protegido(caminho: str) -> bool:
    c = (caminho or "/").rstrip("/") or "/"
    return any(c == p or c.startswith(p + "/") for p in CAMINHOS_PROTEGIDOS)


def _segredo() -> Optional[bytes]:
    s = (settings.PANEL_SESSION_SECRET or "").strip()
    return s.encode() if s else None


def _assinar(expiracao: int) -> str:
    seg = _segredo()
    if not seg:
        raise RuntimeError("PANEL_SESSION_SECRET não configurado.")
    return hmac.new(seg, str(expiracao).encode(), hashlib.sha256).hexdigest()


def emitir_cookie() -> str:
    expiracao = int(time.time()) + DURACAO_SESSAO_SEGUNDOS
    return f"{expiracao}.{_assinar(expiracao)}"


def cookie_valido(valor: Optional[str]) -> bool:
    if not valor:
        return False
    try:
        bruto, assinatura = valor.rsplit(".", 1)
        expiracao = int(bruto)
    except (ValueError, AttributeError):
        return False

    if not _segredo():
        # Falha FECHADO: sem segredo configurado, nenhuma sessão é aceita.
        # Aceitar seria transformar erro de configuração em porta aberta.
        logger.error("[PANEL AUTH] PANEL_SESSION_SECRET ausente; recusando sessões.")
        return False

    if not hmac.compare_digest(_assinar(expiracao), assinatura):
        return False
    return expiracao > int(time.time())


# ---------------------------------------------------------------------------
# SENHA TEMPORARIA EM USO (2026-09-01)
#
# A senha do usuario 'admin' em admin_users foi trocada, a pedido, por uma
# senha FRACA de teste manual: 10 caracteres, padrao previsivel, cairia em
# ataque de dicionario. TROCAR por uma senha forte do gerenciador ANTES de
# qualquer cliente real acessar o sistema -- essa mesma sessao da acesso a
# configuracao de IA, a base de RAG e ao pareamento de WhatsApp de TODOS os
# inquilinos.
#
# O procedimento de troca esta em RESTAURAR-AUTH.md, secao
# "SENHA DO PAINEL E TEMPORARIA". Nao colocar o valor da senha aqui.
# ---------------------------------------------------------------------------


def gerar_hash(senha: str) -> str:
    """bcrypt com salt proprio por senha."""
    import bcrypt

    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def conferir_hash(senha: str, hash_guardado: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw((senha or "").encode("utf-8"), (hash_guardado or "").encode("utf-8"))
    except (ValueError, TypeError):
        # Hash malformado no banco nao pode virar excecao 500 no login.
        logger.error("[PANEL AUTH] Hash de senha malformado no banco.")
        return False


async def autenticar(usuario: Optional[str], senha: Optional[str]):
    """
    Valida usuario + senha contra admin_users.

    Devolve o AdminUser em caso de sucesso, ou None. NAO distingue "usuario
    inexistente" de "senha errada" para quem chama: a resposta ao cliente e
    a mesma, senao o login viraria um oraculo de quais usuarios existem.

    Quando o usuario nao existe, ainda assim rodamos um bcrypt descartavel.
    Sem isso, a resposta para usuario inexistente voltaria em microssegundos
    e a de senha errada em ~250ms -- a diferenca de tempo entregaria a
    existencia da conta.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.admin import AdminUser

    nome = (usuario or "").strip().lower()
    if not nome or not senha:
        return None

    async with AsyncSessionLocal() as sessao:
        alvo = (
            await sessao.execute(
                select(AdminUser).where(AdminUser.username == nome, AdminUser.is_active.is_(True))
            )
        ).scalar_one_or_none()

        if alvo is None:
            conferir_hash(senha, HASH_DESCARTAVEL)
            logger.warning(f"[PANEL AUTH] Login recusado: usuario '{nome}' inexistente.")
            return None

        if not conferir_hash(senha, alvo.password_hash):
            logger.warning(f"[PANEL AUTH] Login recusado: senha incorreta para '{nome}'.")
            return None

        from datetime import datetime, timezone
        alvo.last_login_at = datetime.now(timezone.utc)
        await sessao.commit()
        logger.info(f"[PANEL AUTH] Login efetuado por '{nome}'.")
        return alvo


# Hash valido de uma senha aleatoria, so para gastar o mesmo tempo de CPU
# quando o usuario nao existe (ver autenticar()).
HASH_DESCARTAVEL = "$2b$12$eImiTXuWVxfM37uY4JANjQ.Uu.SxjkqRHMhTxPjNjbxhkQFuxpDaC"


def sessao_ativa(request) -> bool:
    return cookie_valido(request.cookies.get(NOME_COOKIE))
