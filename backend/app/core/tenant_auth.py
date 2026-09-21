"""
Sessão do painel do CLIENTE (tenant).

Espelha `panel_auth` — mesmo HMAC-SHA256, mesma validade, mesmas flags de
cookie — mas com **duas separações deliberadas**:

1. **Cookie com nome próprio** (`atendit_tenant` vs `atendit_painel`). Nomes
   iguais fariam o navegador sobrescrever um com o outro no mesmo domínio:
   entrar como cliente derrubaria a sessão de admin e vice-versa.

2. **Domínio de assinatura próprio** (o prefixo `tenant:v1:` entra na mensagem
   assinada). Sem isso, os dois cookies compartilhariam o mesmo segredo e a
   mesma forma, e um cookie de admin poderia ser apresentado como cookie de
   tenant. O prefixo faz as assinaturas não se cruzarem mesmo com o segredo
   sendo o mesmo.

O hash de senha é reaproveitado de `panel_auth` (`gerar_hash`/`conferir_hash`)
em vez de duplicado: dois lugares implementando bcrypt viram dois lugares para
divergir.
"""

import hashlib
import hmac
import logging
import time
import uuid
from typing import Optional

from app.core.config import settings
from app.core.panel_auth import HASH_DESCARTAVEL, conferir_hash, gerar_hash  # noqa: F401

logger = logging.getLogger("atendit.tenant_auth")

NOME_COOKIE = "atendit_tenant"
DURACAO_SESSAO_SEGUNDOS = 12 * 60 * 60  # 12h, igual ao painel admin
DOMINIO = "tenant:v1"

# Caminhos que exigem sessao DE TENANT. Mesma disciplina de lista por excecao
# do painel admin: o que nao esta aqui passa. /tenant/login fica de fora por
# necessidade obvia -- protege-lo trancaria a porta pelo lado de dentro.
CAMINHOS_PROTEGIDOS = ("/tenant/painel",)


def caminho_protegido(caminho: str) -> bool:
    c = (caminho or "/").rstrip("/") or "/"
    return any(c == p or c.startswith(p + "/") for p in CAMINHOS_PROTEGIDOS)


def _segredo() -> Optional[bytes]:
    s = (settings.PANEL_SESSION_SECRET or "").strip()
    return s.encode() if s else None


def _assinar(mensagem: str) -> str:
    seg = _segredo()
    if not seg:
        raise RuntimeError("PANEL_SESSION_SECRET não configurado.")
    return hmac.new(seg, f"{DOMINIO}:{mensagem}".encode(), hashlib.sha256).hexdigest()


def emitir_cookie(tenant_id: uuid.UUID, usuario_id: uuid.UUID) -> str:
    """Cookie = <tenant_id>.<usuario_id>.<expiração>.<hmac>."""
    expiracao = int(time.time()) + DURACAO_SESSAO_SEGUNDOS
    corpo = f"{tenant_id}.{usuario_id}.{expiracao}"
    return f"{corpo}.{_assinar(corpo)}"


def ler_sessao(valor: Optional[str]) -> Optional[dict]:
    """
    Devolve {'tenant_id', 'usuario_id'} se o cookie for válido, senão None.

    Falha FECHADO em toda situação ambígua: cookie malformado, segredo ausente,
    assinatura errada ou vencido. Aceitar qualquer um deles transformaria erro
    de configuração em porta aberta.
    """
    if not valor:
        return None
    try:
        tid, uid, exp, assinatura = valor.rsplit(".", 3)
        expiracao = int(exp)
    except (ValueError, AttributeError):
        return None

    if not _segredo():
        logger.error("[TENANT AUTH] PANEL_SESSION_SECRET ausente; recusando sessões.")
        return None

    if not hmac.compare_digest(_assinar(f"{tid}.{uid}.{exp}"), assinatura):
        return None
    if expiracao <= int(time.time()):
        return None

    try:
        return {"tenant_id": uuid.UUID(tid), "usuario_id": uuid.UUID(uid)}
    except ValueError:
        return None


def sessao_do_tenant(request) -> Optional[dict]:
    return ler_sessao(request.cookies.get(NOME_COOKIE))


async def autenticar(email: Optional[str], senha: Optional[str]):
    """
    Valida e-mail + senha contra `tenant_users`.

    Devolve (usuario, motivo). `usuario` é None quando falha, e `motivo` é
    um dos: 'credencial' | 'nao_verificado' | 'inativo'.

    'credencial' cobre tanto e-mail inexistente quanto senha errada, de
    propósito: distinguir os dois transformaria o login num oráculo de quais
    contas existem. E, como em `panel_auth`, um bcrypt descartável roda quando
    o e-mail não existe — sem ele a resposta voltaria em microssegundos contra
    ~250ms, e o tempo entregaria a mesma informação.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.tenant_user import TenantUser

    endereco = (email or "").strip().lower()
    if not endereco or not senha:
        return None, "credencial"

    async with AsyncSessionLocal() as sessao:
        alvo = (
            await sessao.execute(select(TenantUser).where(TenantUser.email == endereco))
        ).scalar_one_or_none()

        if alvo is None:
            conferir_hash(senha, HASH_DESCARTAVEL)
            logger.warning(f"[TENANT AUTH] Login recusado: '{endereco}' não existe.")
            return None, "credencial"

        if not conferir_hash(senha, alvo.password_hash):
            logger.warning(f"[TENANT AUTH] Login recusado: senha incorreta para '{endereco}'.")
            return None, "credencial"

        # A checagem de verificacao vem DEPOIS da senha, nao antes: responder
        # "confirme seu e-mail" a quem errou a senha diria a um estranho que
        # aquele endereco tem conta aqui.
        if not alvo.email_verificado:
            logger.info(f"[TENANT AUTH] Login barrado: '{endereco}' ainda não confirmou o e-mail.")
            return None, "nao_verificado"

        if not alvo.is_active:
            logger.warning(f"[TENANT AUTH] Login barrado: conta '{endereco}' inativa.")
            return None, "inativo"

        from datetime import datetime, timezone

        alvo.last_login_at = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(alvo)
        logger.info(f"[TENANT AUTH] Login efetuado por '{endereco}' (tenant {alvo.tenant_id}).")
        return alvo, "ok"
