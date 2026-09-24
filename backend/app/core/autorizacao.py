"""
Autorização por inquilino — o ponto ÚNICO onde se decide quem vê o quê.

Existe porque, desde 2026-09-01, o painel aceita **dois tipos de sessão**:

  admin  (`admin_users`,  cookie `atendit_painel`) -> qualquer inquilino
  tenant (`tenant_users`, cookie `atendit_tenant`) -> SÓ o inquilino dele

> ### 🔴 A REGRA QUE NÃO PODE SER "SIMPLIFICADA"
> Sessão de tenant **nunca** vale para um inquilino diferente do da sessão.
> O identificador do alvo (`slug` ou `tenant_id`) chega em parâmetro que o
> próprio cliente controla — corpo do POST, path da URL. Sem esta conferência,
> trocar uma palavra na requisição dá acesso ao inquilino do vizinho.
>
> Quem for mexer aqui: `exigir_acesso_ao_tenant` **falha fechado**. Toda
> situação ambígua (sem sessão, alvo inexistente, sessão apontando para
> inquilino apagado) recusa. Transformar qualquer um desses num `pass`
> reabre o buraco inteiro.
"""

import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status

from app.core import panel_auth as _admin
from app.core import tenant_auth as _tenant

logger = logging.getLogger("atendit.autorizacao")


def sessao_admin(request) -> bool:
    return _admin.sessao_ativa(request)


def sessao_tenant(request) -> Optional[dict]:
    return _tenant.sessao_do_tenant(request)


def tem_alguma_sessao(request) -> bool:
    return sessao_admin(request) or sessao_tenant(request) is not None


async def _slug_do_tenant_id(tenant_id: uuid.UUID) -> Optional[str]:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as sessao:
        return (
            await sessao.execute(select(Tenant.slug).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()


async def slug_da_sessao(request) -> Optional[str]:
    """Slug do inquilino da sessão de tenant, ou None se não houver."""
    dados = sessao_tenant(request)
    if not dados:
        return None
    return await _slug_do_tenant_id(dados["tenant_id"])


async def exigir_acesso_ao_tenant(
    request,
    *,
    slug: Optional[str] = None,
    tenant_id: Optional[uuid.UUID] = None,
    permitir_token_interno: bool = False,
) -> str:
    """
    Autoriza a requisição para o inquilino alvo. Devolve o papel: 'admin' |
    'tenant' | 'token_interno'.

    Levanta 401 sem sessão e **403 quando a sessão é de outro inquilino** —
    códigos diferentes de propósito: 401 significa "identifique-se", 403
    significa "identificado, mas não é seu". Devolver 404 aqui esconderia a
    existência do recurso, mas confundiria o diagnóstico de quem opera.
    """
    if permitir_token_interno:
        from app.core.config import settings
        import hmac as _hmac

        enviado = request.headers.get("X-Internal-Token")
        esperado = settings.INTERNAL_API_TOKEN or ""
        if enviado and esperado and _hmac.compare_digest(enviado, esperado):
            return "token_interno"

    # Admin passa para qualquer inquilino -- comportamento preservado.
    if sessao_admin(request):
        return "admin"

    dados = sessao_tenant(request)
    if not dados:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão ausente ou expirada. Faça login.",
        )

    da_sessao = dados["tenant_id"]

    # Alvo por UUID: comparacao direta.
    if tenant_id is not None:
        if tenant_id != da_sessao:
            logger.warning(
                f"[AUTZ] BLOQUEADO: sessão do tenant {da_sessao} tentou alcançar "
                f"o tenant {tenant_id} em {request.url.path}."
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Este recurso pertence a outro inquilino.",
            )
        return "tenant"

    # Alvo por slug: resolve o slug DA SESSAO e compara.
    if slug is not None:
        meu = await _slug_do_tenant_id(da_sessao)
        if meu is None:
            # Sessao apontando para inquilino que nao existe mais. Fecha.
            logger.error(f"[AUTZ] Sessão do tenant {da_sessao} aponta para inquilino inexistente.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sessão inválida. Entre novamente.",
            )
        if (slug or "").strip().lower() != meu:
            logger.warning(
                f"[AUTZ] BLOQUEADO: sessão de '{meu}' tentou alcançar '{slug}' "
                f"em {request.url.path}."
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Este recurso pertence a outro inquilino.",
            )
        return "tenant"

    # Sem alvo declarado: a rota nao carrega dado de inquilino nenhum
    # (ex.: /dashboards/<view>, que serve apenas o HTML da tela). Sessao
    # valida basta. Rota que MANIPULA dado tem de passar slug ou tenant_id --
    # chamar sem alvo seria abrir mao da conferencia.
    return "tenant"



# ---------------------------------------------------------------------------
# F1.7 - Matriz de Papéis e Permissões (Dono, Gestor, Operador, Financeiro, Leitura)
# ---------------------------------------------------------------------------
PAPEIS_VALIDOS = ("dono", "gestor", "operador", "financeiro", "leitura")

PAPEIS_PERMISSOES = {
    "dono": {
        "nome": "Dono",
        "descricao": "Acesso total à conta, faturamento, equipe e exclusões",
        "views": ["*"]
    },
    "gestor": {
        "nome": "Gestor",
        "descricao": "Acesso a operações, atendimentos, modelos, canais, IA e equipe (sem financeiro)",
        "views": [
            "faturamento", "configuracao_guiada", "modelos_segmento", "empresa_cadastro", 
            "canais", "ia_config", "gemini_config", "calendar_config", "rag_management", 
            "fila_atendimento", "ecommerce_config", "intel_operacional", "equipe"
        ]
    },
    "operador": {
        "nome": "Operador",
        "descricao": "Acesso à fila de chat e agendamentos",
        "views": ["fila_atendimento", "calendar_config"]
    },
    "financeiro": {
        "nome": "Financeiro",
        "descricao": "Acesso exclusivo ao painel de faturamento e extratos",
        "views": ["faturamento", "intel_operacional"]
    },
    "leitura": {
        "nome": "Leitura",
        "descricao": "Visualização de relatórios e métricas, sem permissão de disparo ou edição",
        "views": ["faturamento", "intel_operacional"]
    }
}

async def obter_perfil_sessao(request) -> dict:
    """Devolve o perfil e papel da sessão atual (admin ou tenant_user)."""
    if sessao_admin(request):
        return {"tipo": "admin", "role": "dono", "email": "admin", "views": ["*"]}

    dados = sessao_tenant(request)
    if not dados:
        return {"tipo": "anonimo", "role": "nenhum", "email": None, "views": []}

    usuario_id = dados.get("usuario_id")
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant_user import TenantUser

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(TenantUser.email, TenantUser.role).where(TenantUser.id == usuario_id)
        )
        usr = res.first()
        if not usr:
            return {"tipo": "tenant", "role": "leitura", "email": None, "views": PAPEIS_PERMISSOES["leitura"]["views"]}
        
        email, role = usr[0], usr[1] or "dono"
        if role not in PAPEIS_PERMISSOES:
            role = "leitura"
            
        return {
            "tipo": "tenant",
            "role": role,
            "nome_role": PAPEIS_PERMISSOES[role]["nome"],
            "email": email,
            "views": PAPEIS_PERMISSOES[role]["views"]
        }

def usuario_pode_acessar_view(perfil: dict, view_name: str) -> bool:
    """Valida se o perfil logado tem permissão para carregar a view solicitada."""
    views = perfil.get("views", [])
    if "*" in views:
        return True
    return view_name in views

