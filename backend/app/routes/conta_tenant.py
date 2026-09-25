"""
Conta do cliente (tenant): cadastro real, confirmação de e-mail, login e
recuperação de senha.

Substitui a fachada anterior, em que `POST /v1/auth/register` gravava num
arquivo JSON, **descartava a senha em silêncio** e devolvia um "token"
literal previsível (`atendit-token-<slug>-2026`).

## O que um tenant novo NÃO ganha aqui

Nada de WhatsApp e nada de calendário. Instância da Evolution, pareamento de
número e OAuth de agenda continuam sendo **passo manual do próprio cliente**,
no painel dele, depois do primeiro login. Criar instância no cadastro geraria
uma instância órfã por formulário abandonado — e cada uma delas nasce com a
chave global da Evolution embutida como token (ver SEC-09).
"""

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core import tenant_auth as _sessao
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.feature_flags import is_flag_enabled
from app.models.tenant import Tenant
from app.models.tenant_user import TenantUser
from app.services.email_service import enviar_email, montar_html

logger = logging.getLogger("atendit.conta_tenant")
router = APIRouter(tags=["Conta do cliente"])

VALIDADE_RESET = timedelta(hours=1)

# Slugs que colidiriam com rotas reais do sistema.
RESERVADOS = {
    "api", "docs", "login", "logout", "health", "cadastro", "tenant",
    "dashboards", "webhook", "calendar", "admin", "v1", "static", "reset-senha",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _slugificar(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (texto or "").lower()).strip("-")
    return s or "empresa"


async def _slug_unico(sessao, base: str) -> str:
    """
    Resolve colisão com sufixo numérico: empresa, empresa-2, empresa-3...

    Consulta o BANCO, não o JSON legado. O JSON continua existindo e sendo lido
    por `/v1/tenants/*` (painel admin), mas ele não é mais a autoridade sobre
    qual slug está tomado.
    """
    candidato = base if base not in RESERVADOS else f"{base}-app"
    n = 1
    while True:
        existe = (
            await sessao.execute(select(Tenant.id).where(Tenant.slug == candidato))
        ).first()
        if existe is None:
            return candidato
        n += 1
        candidato = f"{base}-{n}"


def _pagina(titulo: str, corpo_html: str, codigo: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=codigo, content=f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATENDIT — {titulo}</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px}}
  .cartao{{width:100%;max-width:420px;background:#161b22;border:1px solid #263041;
          border-radius:12px;padding:34px}}
  .marca{{font-size:19px;font-weight:700;color:#22b8cf;letter-spacing:.5px;margin:0 0 22px}}
  h1{{font-size:20px;margin:0 0 14px}}
  p{{font-size:14.5px;line-height:1.6;color:#9aa7b6;margin:0 0 14px}}
  label{{display:block;font-size:13px;color:#9aa7b6;margin:14px 0 6px}}
  input{{width:100%;padding:11px 13px;border-radius:7px;border:1px solid #2d3748;
        background:#0d1117;color:#e6edf3;font-size:15px}}
  input:focus{{outline:none;border-color:#22b8cf}}
  button{{width:100%;margin-top:20px;padding:12px;border:0;border-radius:7px;background:#22b8cf;
         color:#04222a;font-size:15px;font-weight:700;cursor:pointer}}
  button:disabled{{opacity:.6;cursor:default}}
  a{{color:#22b8cf;text-decoration:none;font-size:13.5px}}
  a:hover{{text-decoration:underline}}
  .rodape{{margin-top:18px;display:flex;justify-content:space-between;gap:12px}}
  .aviso{{margin-top:16px;padding:11px 13px;border-radius:7px;font-size:13.5px;display:none}}
  .erro{{background:#3b1219;border:1px solid #7d2230;color:#ffb3bd}}
  .ok{{background:#0f2f22;border:1px solid #1d6b4a;color:#7ee2b8}}
</style></head><body><div class="cartao">
<p class="marca">ATENDIT</p>
{corpo_html}
</div></body></html>""")


# ---------------------------------------------------------------------------
# cadastro (chamado por POST /v1/auth/register, em main.py)
# ---------------------------------------------------------------------------
async def registrar_conta(corpo: dict, tarefas: BackgroundTasks) -> JSONResponse:
    nome = (corpo.get("name") or corpo.get("nome") or "").strip()
    email = (corpo.get("email") or "").strip().lower()
    senha = corpo.get("password") or corpo.get("senha") or ""
    slug_pedido = (corpo.get("slug") or "").strip().lower()

    if not nome:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Informe o nome da empresa."})
    if "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Informe um e-mail válido."})
    if len(senha) < 8:
        return JSONResponse(status_code=400, content={"status": "error", "message": "A senha precisa de pelo menos 8 caracteres."})

    token = secrets.token_urlsafe(32)[:64]

    async with AsyncSessionLocal() as sessao:
        # E-mail duplicado responde 409 com mensagem propria. Aqui NAO vale a
        # regra de resposta generica do login: no cadastro o proprio usuario
        # precisa saber que ja tem conta, senao fica preso sem entender.
        ja = (
            await sessao.execute(select(TenantUser.id).where(TenantUser.email == email))
        ).first()
        if ja is not None:
            return JSONResponse(status_code=409, content={
                "status": "error",
                "message": "Já existe uma conta com este e-mail. Tente entrar ou recuperar a senha.",
            })

        slug = await _slug_unico(sessao, _slugificar(slug_pedido or nome))
        dias = int(getattr(settings, "DEFAULT_TRIAL_DAYS", 7) or 7)

        # UMA transacao: ou nascem os dois, ou nenhum. Um tenant sem conta de
        # acesso seria um inquilino que ninguem consegue abrir, e uma conta sem
        # tenant seria login que nao leva a lugar nenhum.
        try:
            inquilino = Tenant(
                name=nome[:255],
                slug=slug,
                admin_email=email[:255],
                trial_days=dias,
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=dias),
                is_active=True,
                meta_data={"origem": "cadastro_publico"},
            )
            sessao.add(inquilino)
            await sessao.flush()          # gera o id sem fechar a transacao

            conta = TenantUser(
                tenant_id=inquilino.id,
                email=email,
                password_hash=_sessao.gerar_hash(senha),
                is_active=True,
                email_verificado=False,
                token_verificacao=token,
            )
            sessao.add(conta)
            await sessao.commit()
            tenant_id, slug_final = str(inquilino.id), inquilino.slug
        except Exception as exc:
            await sessao.rollback()
            logger.error(f"[CADASTRO] Falha ao criar conta para '{email}': {exc}")
            return JSONResponse(status_code=500, content={
                "status": "error", "message": "Não consegui concluir o cadastro. Tente novamente."})

    logger.info(f"[CADASTRO] Tenant '{slug_final}' ({tenant_id}) e conta '{email}' criados.")

    # E-mail em segundo plano: o cadastro JA esta gravado, e o usuario nao deve
    # esperar o Resend para ver a tela de sucesso.
    tarefas.add_task(_enviar_verificacao, email, nome, token)

    return JSONResponse(status_code=201, content={
        "status": "success",
        "tenant_slug": slug_final,
        "message": "Cadastro criado. Confira seu e-mail para confirmar o endereço e liberar o acesso.",
    })


async def _enviar_verificacao(email: str, nome: str, token: str) -> None:
    link = f"{settings.URL_PUBLICA}/v1/auth/verificar?token={token}"
    html = montar_html(
        "Confirme seu e-mail",
        [f"Olá! A conta da <strong>{nome}</strong> foi criada no ATENDIT.",
         "Falta confirmar este endereço de e-mail. Só depois disso o acesso ao painel é liberado."],
        "Confirmar meu e-mail", link,
    )
    ok = await enviar_email(email, "ATENDIT — confirme seu e-mail", html)
    if not ok:
        # A conta existe e o token esta gravado. Reenviar e possivel; perder o
        # cadastro por causa do e-mail, nao.
        logger.error(f"[CADASTRO] Conta de '{email}' CRIADA, mas o e-mail de verificação falhou.")


# ---------------------------------------------------------------------------
# verificação
# ---------------------------------------------------------------------------
@router.get("/v1/auth/verificar", include_in_schema=False)
async def verificar_email(token: str = ""):
    if not token:
        return _pagina("Link inválido",
            "<h1>Link inválido</h1><p>Este endereço não tem um código de confirmação.</p>"
            '<div class="rodape"><a href="/tenant/login">Ir para o login</a></div>', 400)

    async with AsyncSessionLocal() as sessao:
        conta = (
            await sessao.execute(select(TenantUser).where(TenantUser.token_verificacao == token))
        ).scalar_one_or_none()

        if conta is None:
            # Token ja usado tambem cai aqui, porque ele e apagado no uso. A
            # mensagem cobre os dois casos sem afirmar qual foi.
            return _pagina("Link expirado",
                "<h1>Link inválido ou já usado</h1>"
                "<p>Se você já confirmou seu e-mail, é só entrar normalmente.</p>"
                '<div class="rodape"><a href="/tenant/login">Ir para o login</a></div>', 400)

        conta.email_verificado = True
        conta.token_verificacao = None      # uso unico
        await sessao.commit()
        endereco = conta.email

    logger.info(f"[VERIFICACAO] E-mail '{endereco}' confirmado.")
    return _pagina("E-mail confirmado",
        "<h1>E-mail confirmado ✅</h1>"
        "<p>Sua conta está ativa. Você já pode entrar no painel.</p>"
        "<p>O WhatsApp e a agenda ainda não estão conectados — isso é feito por você "
        "mesmo, dentro do painel, quando quiser.</p>"
        '<a href="/tenant/login"><button type="button">Entrar no painel</button></a>')


# ---------------------------------------------------------------------------
# login do tenant
# ---------------------------------------------------------------------------
@router.get("/tenant/login", include_in_schema=False)
async def tela_login_tenant():
    return _pagina("Entrar", """
<h1>Entrar no seu painel</h1>
<p>Acesso do cliente. O painel administrativo do ATENDIT fica em outro endereço.</p>
<form id="f" autocomplete="on">
  <label for="email">E-mail</label>
  <input id="email" type="email" required autocomplete="username" value="manfredhaut@gmail.com">
  <label for="senha">Senha</label>
  <input id="senha" type="password" required autocomplete="current-password" value="Admin@2026">
  <button id="b" type="submit">Entrar</button>
</form>
<div id="aviso" class="aviso erro"></div>
<div class="rodape">
  <a href="/v1/auth/esqueci-senha-form">Esqueci minha senha</a>
  <a href="/cadastro">Criar conta</a>
</div>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const b = document.getElementById('b'), av = document.getElementById('aviso');
  b.disabled = true; b.textContent = 'Entrando...'; av.style.display = 'none';
  try {
    const r = await fetch('/tenant/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email.value, senha: senha.value})
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok) { window.location = d.destino || '/tenant/painel'; return; }
    av.textContent = d.detail || 'Não consegui entrar.'; av.style.display = 'block';
  } catch (_) {
    av.textContent = 'Falha de conexão. Tente de novo.'; av.style.display = 'block';
  }
  b.disabled = false; b.textContent = 'Entrar';
});
</script>""")


@router.post("/tenant/login", include_in_schema=False)
async def login_tenant(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}

    conta, motivo = await _sessao.autenticar(corpo.get("email"), corpo.get("senha"))

    if conta is None:
        if motivo == "nao_verificado":
            return JSONResponse(status_code=403, content={
                "detail": "Confirme seu e-mail antes de entrar. Procure a mensagem "
                          "que enviamos no seu cadastro — o link libera o acesso."})
        if motivo == "inativo":
            return JSONResponse(status_code=403, content={
                "detail": "Esta conta está desativada. Fale com o suporte."})
        # Mensagem UNICA para e-mail inexistente e senha errada.
        return JSONResponse(status_code=401, content={"detail": "E-mail ou senha incorretos."})

    resposta = JSONResponse(content={"ok": True, "destino": "/tenant/painel"})
    resposta.set_cookie(
        _sessao.NOME_COOKIE,
        _sessao.emitir_cookie(conta.tenant_id, conta.id),
        max_age=_sessao.DURACAO_SESSAO_SEGUNDOS,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return resposta


@router.get("/tenant/logout", include_in_schema=False)
@router.post("/tenant/logout", include_in_schema=False)
async def logout_tenant():
    r = RedirectResponse("/tenant/login", status_code=303)
    r.delete_cookie(_sessao.NOME_COOKIE, path="/")
    return r


# ---------------------------------------------------------------------------
# painel do tenant (protegido pelo middleware)
# ---------------------------------------------------------------------------
@router.get("/tenant/painel", include_in_schema=False)
async def painel_tenant(request: Request):
    dados = _sessao.sessao_do_tenant(request)
    if not dados:
        return RedirectResponse("/tenant/login", status_code=303)

    async with AsyncSessionLocal() as sessao:
        inquilino = (
            await sessao.execute(select(Tenant).where(Tenant.id == dados["tenant_id"]))
        ).scalar_one_or_none()
        conta = (
            await sessao.execute(select(TenantUser).where(TenantUser.id == dados["usuario_id"]))
        ).scalar_one_or_none()

        if inquilino is None or conta is None:
            r = RedirectResponse("/tenant/login", status_code=303)
            r.delete_cookie(_sessao.NOME_COOKIE, path="/")
            return r

        # Checagem de Feature Flag (F0.11 / F1.1)
        menu_param = request.query_params.get("menu")
        flag_core = await is_flag_enabled("presenthia_core", sessao, tenant_id=inquilino.id)
        
        # O piloto manitest ou quem explicitamente pedir ?menu=novo verá a navegação V2 (14 abas)
        usar_menu_v2 = (menu_param == "novo") or (flag_core and menu_param != "legado")

    slug = inquilino.slug

    # Menu Legado de 10 Abas (Fallback / Inquilinos comuns)
    menu_legado_html = '''
    <nav class="menu" id="menu">
      <a class="item ativo" data-v="inicio">📊 Painel Principal</a>
      <a class="item" data-v="empresa_cadastro">🏢 Cadastro da Empresa</a>
      <a class="item" data-v="ia_config">⚙️ Configurações do Atendente</a>
      <a class="item" data-v="canais">📡 Canais WhatsApp</a>
      <a class="item" data-v="calendar_config">📅 Configuração do Calendário</a>
      <a class="item" data-v="rag_management">📁 Gestão de Documentos</a>
      <a class="item" data-v="fila_atendimento">💬 Atendimento</a>
      <a class="item" data-v="gemini_config">🧠 Configuração da IA</a>
      <a class="item" data-v="ecommerce_config">🛒 Loja & E-Commerce</a>
      <a class="item" data-v="intel_operacional">📊 Inteligência Comercial & Operacional</a>
    </nav>
    <div class="rodape">
      <a href="/tenant/painel?menu=novo" style="font-size: 11px; color: var(--p-turquesa, #3ccbc5); display: block; margin-bottom: 8px;">✨ Experimentar Novo Menu V2</a>
      <a href="/tenant/logout">← Sair da conta</a>
    </div>
    '''

    # Menu Oficial Presenthia V2 de 14 Abas (com duplo rótulo temporário F1.1)
    menu_v2_html = '''
    <nav class="menu" id="menu">
      <a class="item ativo" data-v="inicio"><span class="m-icon">🏠</span> 1. Início</a>
      <a class="item" data-v="fila_atendimento"><span class="m-icon">💬</span> 2. Atendimento</a>
      <a class="item" data-v="leads"><span class="m-icon">🎯</span> 3. Aquisição</a>
      <a class="item" data-v="leads"><span class="m-icon">📊</span> 4. CRM & Funil</a>
      <a class="item" data-v="calendar_config"><span class="m-icon">📅</span> 5. Agenda <small class="sub-legado">antes: Configuração do Calendário</small></a>
      <a class="item" data-v="equipe"><span class="m-icon">👥</span> 6. Profissionais & Escalas</a>
      <a class="item" data-v="ecommerce_config"><span class="m-icon">🛍️</span> 7. Vitrine & Loja <small class="sub-legado">antes: Loja & E-Commerce</small></a>
      <a class="item" data-v="video"><span class="m-icon">📹</span> 8. Consultoria por Vídeo</a>
      <a class="item" data-v="ia_config"><span class="m-icon">🤖</span> 9. Assistente IA <small class="sub-legado">antes: Config. Atendente + IA</small></a>
      <a class="item" data-v="rag_management"><span class="m-icon">📚</span> 10. Base de Conhecimento <small class="sub-legado">antes: Gestão de Documentos</small></a>
      <a class="item" data-v="canais"><span class="m-icon">📡</span> 11. Canais</a>
      <a class="item" data-v="faturamento"><span class="m-icon">💳</span> 12. Financeiro</a>
      <a class="item" data-v="intel_operacional"><span class="m-icon">📈</span> 13. Inteligência Operacional</a>
      <a class="item" data-v="empresa_cadastro"><span class="m-icon">🏢</span> 14. Empresa & Conta <small class="sub-legado">antes: Cadastro da Empresa</small></a>
    </nav>
    <div class="rodape">
      <a href="/tenant/painel?menu=legado" style="font-size: 11px; color: #a1a1aa; display: block; margin-bottom: 8px;">↩ Voltar ao menu anterior</a>
      <a href="/tenant/logout">← Sair da conta</a>
    </div>
    '''

    nav_escolhida = menu_v2_html if usar_menu_v2 else menu_legado_html

    return HTMLResponse(content=f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PRESENTHIA — {inquilino.name}</title>
<link rel="stylesheet" href="/static/brand/tokens.css">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;display:flex;min-height:100vh;background:var(--p-marfim, #fbf8f4);
       font-family:var(--p-fonte-texto, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif);color:var(--p-texto, #231726)}}
  .lateral{{width:280px;background:var(--p-berinjela, #231726);color:#DCD2DF;display:flex;flex-direction:column;
           flex-shrink:0;box-shadow: 2px 0 8px rgba(0,0,0,0.06);}}
  .marca{{padding:22px 20px;font-family:var(--p-fonte-titulo, Georgia, serif);font-size:22px;font-weight:400;color:var(--p-turquesa, #3ccbc5);letter-spacing:.5px;
         border-bottom:1px solid rgba(255,255,255,0.08);display: flex;align-items: center;justify-content: space-between;}}
  .marca-tag{{font-size: 10px; background: rgba(60, 203, 197, 0.15); color: var(--p-turquesa, #3ccbc5); padding: 2px 8px; border-radius: 999px; font-weight: 700; letter-spacing: 0.5px;}}
  .menu{{padding:14px 0;flex:1;overflow-y:auto;max-height:calc(100vh - 140px);}}
  .item{{display:flex;align-items:center;gap:10px;padding:9px 18px;font-size:13.2px;color:#B5A8BA;cursor:pointer;
        border-left:3px solid transparent;transition:all 0.15s ease;text-decoration:none;}}
  .item:hover{{background:rgba(255,255,255,0.06);color:#ffffff}}
  .item.ativo{{background:#3A2740;color:var(--p-turquesa, #3ccbc5);border-left-color:var(--p-turquesa, #3ccbc5);font-weight:600}}
  .sub-legado{{display:block;font-size:10px;color:#8e8293;font-weight:400;margin-top:1px;}}
  .rodape{{padding:16px 20px;border-top:1px solid rgba(255,255,255,0.08)}}
  .rodape a{{color:#B5A8BA;font-size:12.5px;text-decoration:none;transition:color 0.15s ease}}
  .rodape a:hover{{color:var(--p-turquesa, #3ccbc5)}}
  .principal{{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--p-marfim, #fbf8f4)}}
  .topo{{background:#fff;border-bottom:1px solid var(--p-borda, #eae3dc);padding:14px 26px;display:flex;
        align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}}
  .topo h1{{font-family:var(--p-fonte-titulo, Georgia, serif);font-size:19px;margin:0;color:var(--p-texto, #231726)}}
  .cracha{{background:var(--p-sucesso-fundo, #e2f6f5);color:var(--p-turquesa-texto, #0b7570);border:1px solid var(--p-borda, #eae3dc);border-radius:999px;
          padding:5px 14px;font-size:12px;font-weight:600}}
  .sair{{padding:6px 16px;background:#fff;color:#b91c1c;border:1px solid #fecaca;
        border-radius:999px;font-size:12px;font-weight:600;text-decoration:none;transition:background 0.15s ease}}
  .sair:hover{{background:#fff5f5}}
  .area{{padding:24px;overflow:auto;flex:1}}
  .btn-busca-topo{{display:flex;align-items:center;gap:10px;background:#fbf8f4;border:1px solid #eae3dc;padding:6px 14px;border-radius:10px;font-size:12.5px;color:#6b7280;cursor:pointer;transition:all 0.15s ease}}
  .btn-busca-topo:hover{{background:#f4eee7;border-color:#dcd2df;color:#231726}}
  .btn-busca-topo kbd{{background:#fff;border:1px solid #dcd2df;border-radius:5px;padding:2px 6px;font-size:10px;font-weight:700;color:#5e5563}}
  .busca-overlay{{position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(35,23,38,0.5);backdrop-filter:blur(3px);z-index:9999;display:none;align-items:flex-start;justify-content:center;padding-top:12vh}}
  .busca-modal{{background:#ffffff;width:100%;max-width:620px;border-radius:16px;box-shadow:0 20px 40px rgba(0,0,0,0.18);border:1px solid #eae3dc;overflow:hidden}}
  .busca-campo-wrap{{display:flex;align-items:center;padding:16px 20px;border-bottom:1px solid #eae3dc;gap:12px}}
  .busca-input{{flex:1;border:none;outline:none;font-size:15px;color:#231726;background:transparent}}
  .busca-lista{{max-height:360px;overflow-y:auto;padding:8px}}
  .busca-item{{display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:10px;cursor:pointer;text-decoration:none}}
  .busca-item:hover, .busca-item.selecionado{{background:rgba(60,203,197,0.12)}}
  .busca-item-icon{{font-size:18px;width:24px;text-align:center}}
  .busca-item-info{{flex:1}}
  .busca-item-titulo{{font-size:13.5px;font-weight:600;color:#231726}}
  .busca-item-sub{{font-size:11.5px;color:#6b7280;margin-top:2px}}
  .busca-item-badge{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;background:#fbf8f4;border:1px solid #eae3dc;color:#5e5563}}
  .busca-rodape{{display:flex;justify-content:space-between;align-items:center;padding:10px 20px;background:#fbf8f4;border-top:1px solid #eae3dc;font-size:11px;color:#8e8293}}
  .busca-rodape kbd{{background:#ffffff;border:1px solid #dcd2df;border-radius:4px;padding:2px 5px;font-size:10px}}
  @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .fade-in {{ animation: fadeIn 0.25s ease-in-out; }}
</style></head><body>
<div class="lateral">
  <div class="marca">
    <span>PRESENTHIA</span>
    <span class="marca-tag">{'V2' if usar_menu_v2 else 'V1'}</span>
  </div>
  {nav_escolhida}
</div>
<div class="principal">
  <header class="topo">
    <h1 id="titulo">{inquilino.name}</h1>
    <div style="display:flex;align-items:center;gap:12px">
      <button type="button" class="btn-busca-topo" onclick="window.abrirModalBusca()">
        <i class="bi bi-search"></i>
        <span>Buscar ou ir para...</span>
        <kbd>Ctrl+K</kbd>
      </button>
      <span class="cracha">{slug}</span>
      <span style="font-size:12.5px;color:#6b7280">{conta.email}</span>
      <a class="sair" href="/tenant/logout">Sair</a>
    </div>
  </header>
  <div class="area" id="conteudo"></div>
</div>
<script>
window.currentTenantSlug = {slug!r};
window.currentTenantData = {{ id: {str(inquilino.id)!r}, slug: window.currentTenantSlug, name: {inquilino.name!r} }};

const htmlInicio = `<div class="fade-in" style="display: flex; flex-direction: column; gap: 24px; max-width: 1000px;">
    <div style="background: #ffffff; padding: 26px 30px; border-radius: var(--p-raio, 16px); border: 1px solid var(--p-borda, #eae3dc); box-shadow: 0 1px 3px rgba(0,0,0,0.02); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;">
      <div>
        <h2 style="font-family: var(--p-fonte-titulo, Georgia, serif); font-size: 1.6rem; color: var(--p-texto, #231726); margin: 0 0 4px 0;">Bem-vindo, {inquilino.name}</h2>
        <p style="font-size: 0.88rem; color: var(--p-texto-suave, #5e5563); margin: 0;">Painel de Controle Unificado Presenthia — Atendimento inteligente, canais oficiais e conversão.</p>
      </div>
      <span style="background: var(--p-sucesso-fundo, #e2f6f5); color: var(--p-turquesa-texto, #0b7570); font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 999px;">
        Tenant: {slug}
      </span>
    </div>

    <!-- CARTÕES DE ATENÇÃO AGORA (ITEM F1.2) -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px;">
      <div style="background: #ffffff; padding: 18px 20px; border-radius: var(--p-raio, 16px); border: 1px solid var(--p-borda, #eae3dc); border-left: 4px solid var(--p-magenta, #D0006F);">
        <div style="font-size: 0.72rem; font-weight: 700; color: #64748b; text-transform: uppercase;">Atendimentos Pendentes</div>
        <div style="font-size: 1.6rem; font-weight: 800; color: var(--p-texto, #231726); margin-top: 4px;">0</div>
        <div style="font-size: 0.78rem; color: var(--p-turquesa-texto, #0b7570); margin-top: 4px; font-weight: 600; cursor: pointer;" onclick="document.querySelector('[data-v=fila_atendimento]')?.click()">Ver fila de espera →</div>
      </div>

      <div style="background: #ffffff; padding: 18px 20px; border-radius: var(--p-raio, 16px); border: 1px solid var(--p-borda, #eae3dc); border-left: 4px solid var(--p-turquesa, #3ccbc5);">
        <div style="font-size: 0.72rem; font-weight: 700; color: #64748b; text-transform: uppercase;">Status do WhatsApp</div>
        <div style="font-size: 1.1rem; font-weight: 700; color: var(--p-texto, #231726); margin-top: 6px;">Pronto para Conectar</div>
        <div style="font-size: 0.78rem; color: var(--p-turquesa-texto, #0b7570); margin-top: 4px; font-weight: 600; cursor: pointer;" onclick="document.querySelector('[data-v=canais]')?.click()">Gerenciar canais →</div>
      </div>

      <div style="background: #ffffff; padding: 18px 20px; border-radius: var(--p-raio, 16px); border: 1px solid var(--p-borda, #eae3dc); border-left: 4px solid var(--p-ambar, #e3a028);">
        <div style="font-size: 0.72rem; font-weight: 700; color: #64748b; text-transform: uppercase;">Sincronização de Agenda</div>
        <div style="font-size: 1.1rem; font-weight: 700; color: var(--p-texto, #231726); margin-top: 6px;">Calendly / Google</div>
        <div style="font-size: 0.78rem; color: #b45309; margin-top: 4px; font-weight: 600; cursor: pointer;" onclick="document.querySelector('[data-v=calendar_config]')?.click()">Ajustar grade →</div>
      </div>
    </div>

    <!-- CONFIGURAÇÃO GUIADA E ATALHOS RÁPIDOS -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
      <div style="padding: 20px; background: #ffffff; border: 1px solid var(--p-borda, #eae3dc); border-radius: var(--p-raio, 16px);">
        <div style="font-weight: 700; font-size: 0.95rem; color: var(--p-texto, #231726); margin-bottom: 6px;">📱 Canais de WhatsApp</div>
        <p style="font-size: 0.82rem; color: var(--p-texto-suave, #5e5563); margin: 0 0 14px 0;">Conecte via Evolution API (QR Code) ou configure a API Oficial da Meta com faturamento direto.</p>
        <div style="display: flex; gap: 12px;">
          <a href="javascript:void(0)" onclick="carregarView('canais', document.querySelector('[data-v=canais]')).then(() => window.abrirCanaisSubaba && window.abrirCanaisSubaba('qrcode'))" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Conectar QR Code ↗</a>
          <a href="javascript:void(0)" onclick="carregarView('canais', document.querySelector('[data-v=canais]')).then(() => window.abrirCanaisSubaba && window.abrirCanaisSubaba('meta'))" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Configurar Meta ↗</a>
        </div>
      </div>

      <div style="padding: 20px; background: #ffffff; border: 1px solid var(--p-borda, #eae3dc); border-radius: var(--p-raio, 16px);">
        <div style="font-weight: 700; font-size: 0.95rem; color: var(--p-texto, #231726); margin-bottom: 6px;">📅 Agendamento Integrado</div>
        <p style="font-size: 0.82rem; color: var(--p-texto-suave, #5e5563); margin: 0 0 14px 0;">Sincronize com agendas para que o atendente IA consulte horários livres e marque compromissos.</p>
        <div style="display: flex; gap: 14px;">
          <a href="/calendar/oauth/google/start?tenant={slug}" target="_blank" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Google Calendar ↗</a>
          <a href="/calendar/oauth/microsoft/start?tenant={slug}" target="_blank" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Microsoft 365 ↗</a>
        </div>
      </div>
    </div>
  </div>`;

async function carregarView(nome, el) {{
  document.querySelectorAll('.item').forEach(e => e.classList.remove('ativo'));
  if (el) el.classList.add('ativo');
  const area = document.getElementById('conteudo');

  if (nome === 'inicio') {{
    area.innerHTML = htmlInicio;
    document.getElementById('titulo').textContent = {inquilino.name!r};
    return;
  }}

  area.innerHTML = '<div style="padding: 30px; text-align: center; color:#6b7280; font-weight: 600;">Carregando módulo...</div>';
  try {{
    const r = await fetch('/dashboards/' + nome);
    if (r.status === 401 || r.status === 403) {{ window.location = '/tenant/login'; return; }}
    
    // Suporta tanto texto HTML direto quanto JSON embrulhado
    const txt = await r.text();
    let html;
    try {{
      html = JSON.parse(txt);
    }} catch (_) {{
      html = txt;
    }}

    area.innerHTML = html;
    area.querySelectorAll('script').forEach(antigo => {{
      const novo = document.createElement('script');
      if (antigo.src) novo.src = antigo.src; else novo.textContent = antigo.textContent;
      antigo.replaceWith(novo);
    }});
    document.getElementById('titulo').textContent =
      (el ? el.textContent.replace(/^[^A-Za-zÀ-ÿ0-9]+/, '').trim() : {inquilino.name!r});
  }} catch (e) {{
    area.innerHTML = '<div style="background:#fff; padding:24px; border-radius:12px; border:1px solid #eae3dc;"><h2>Não foi possível abrir esta tela</h2>'
      + '<p style="color:#6b7280;">Tente novamente em instantes ou utilize o menu anterior.</p></div>';
  }}
}}

document.getElementById('menu').addEventListener('click', (ev) => {{
  const item = ev.target.closest('.item');
  if (item && item.dataset.v) carregarView(item.dataset.v, item);
}});

// Carrega a tela inicial por padrão
window.addEventListener('DOMContentLoaded', () => {{
  carregarView('inicio', document.querySelector('.item.ativo'));
}});

// Item F1.6: Busca Global e Atalhos Ctrl+K
let indiceBusca = 0;
let timerBusca = null;

window.abrirModalBusca = function() {{
  const modal = document.getElementById('modal-busca-global');
  const input = document.getElementById('campo-busca-global');
  if (modal && input) {{
    modal.style.display = 'flex';
    input.value = '';
    input.focus();
    executarBusca('');
  }}
}};

window.fecharModalBusca = function() {{
  const modal = document.getElementById('modal-busca-global');
  if (modal) modal.style.display = 'none';
}};

window.addEventListener('keydown', (e) => {{
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {{
    e.preventDefault();
    const modal = document.getElementById('modal-busca-global');
    if (modal && modal.style.display === 'flex') {{
      window.fecharModalBusca();
    }} else {{
      window.abrirModalBusca();
    }}
  }} else if (e.key === 'Escape') {{
    window.fecharModalBusca();
  }}
}});

async function executarBusca(termo) {{
  const lista = document.getElementById('busca-resultados-lista');
  if (termo.trim().length === 0) {{
    lista.innerHTML = '<div style="padding: 10px 14px; font-size: 11px; font-weight: 700; color: #8e8293; text-transform: uppercase;">Acesso Rápido às 14 Abas</div>'
      + '<div class="busca-item selecionado" onclick="selecionarAbaBusca(\'inicio\')"><span class="busca-item-icon">🏠</span><div class="busca-item-info"><div class="busca-item-titulo">1. Início</div><div class="busca-item-sub">Painel Principal e visão geral</div></div><span class="busca-item-badge">Aba</span></div>'
      + '<div class="busca-item" onclick="selecionarAbaBusca(\'fila_atendimento\')"><span class="busca-item-icon">💬</span><div class="busca-item-info"><div class="busca-item-titulo">2. Atendimento</div><div class="busca-item-sub">Fila e conversas ao vivo</div></div><span class="busca-item-badge">Aba</span></div>'
      + '<div class="busca-item" onclick="selecionarAbaBusca(\'leads\')"><span class="busca-item-icon">🎯</span><div class="busca-item-info"><div class="busca-item-titulo">3. Aquisição & 4. CRM</div><div class="busca-item-sub">Gestão de oportunidades e funil</div></div><span class="busca-item-badge">Aba</span></div>'
      + '<div class="busca-item" onclick="selecionarAbaBusca(\'calendar_config\')"><span class="busca-item-icon">📅</span><div class="busca-item-info"><div class="busca-item-titulo">5. Agenda</div><div class="busca-item-sub">Integrações de calendário</div></div><span class="busca-item-badge">Aba</span></div>'
      + '<div class="busca-item" onclick="selecionarAbaBusca(\'canais\')"><span class="busca-item-icon">📡</span><div class="busca-item-info"><div class="busca-item-titulo">11. Canais WhatsApp</div><div class="busca-item-sub">Oficial Meta e QR Code</div></div><span class="busca-item-badge">Aba</span></div>'
      + '<div class="busca-item" onclick="selecionarAbaBusca(\'video\')"><span class="busca-item-icon">📹</span><div class="busca-item-info"><div class="busca-item-titulo">8. Consultoria por Vídeo</div><div class="busca-item-sub">Salas de conferência WebRTC</div></div><span class="busca-item-badge">Aba</span></div>';
    indiceBusca = 0;
    return;
  }}

  try {{
    const res = await fetch('/api/busca-global?q=' + encodeURIComponent(termo));
    const data = await res.json();
    if (data.resultados === undefined || data.resultados.length === 0) {{
      lista.innerHTML = '<div style="padding: 24px; text-align: center; font-size: 13px; color: #8e8293;">Nenhum resultado encontrado para "' + termo + '".</div>';
      return;
    }}
    indiceBusca = 0;
    let htmlItens = '';
    for (let i = 0; i < data.resultados.length; i++) {{
      const r = data.resultados[i];
      const sel = (i === 0) ? ' selecionado' : '';
      const sub = r.subtitulo ? r.subtitulo : '';
      const subaba = r.subaba ? r.subaba : '';
      htmlItens += '<div class="busca-item' + sel + '" onclick="executarItemBusca(\'' + r.acao + '\', \'' + subaba + '\')">'
        + '<span class="busca-item-icon">' + (r.icone || '🔍') + '</span>'
        + '<div class="busca-item-info"><div class="busca-item-titulo">' + r.titulo + '</div><div class="busca-item-sub">' + sub + '</div></div>'
        + '<span class="busca-item-badge">' + r.tipo + '</span></div>';
    }}
    lista.innerHTML = htmlItens;
  }} catch (_) {{
    lista.innerHTML = '<div style="padding: 16px; color: #b91c1c; font-size: 12px;">Erro ao buscar resultados.</div>';
  }}
}}

window.selecionarAbaBusca = function(view) {{
  window.fecharModalBusca();
  const el = document.querySelector('[data-v="' + view + '"]');
  carregarView(view, el);
}};

window.executarItemBusca = function(acao, subaba) {{
  window.fecharModalBusca();
  const el = document.querySelector('[data-v="' + acao + '"]');
  carregarView(acao, el).then(() => {{
    if (subaba && window.abrirCanaisSubaba) {{
      setTimeout(() => window.abrirCanaisSubaba(subaba), 150);
    }}
  }});
}};

window.addEventListener('DOMContentLoaded', () => {{
  const campo = document.getElementById('campo-busca-global');
  if (campo) {{
    campo.addEventListener('input', (e) => {{
      clearTimeout(timerBusca);
      timerBusca = setTimeout(() => executarBusca(e.target.value), 200);
    }});
    campo.addEventListener('keydown', (e) => {{
      const itens = document.querySelectorAll('.busca-item');
      if (itens.length === 0) return;
      if (e.key === 'ArrowDown') {{
        e.preventDefault();
        itens[indiceBusca] && itens[indiceBusca].classList.remove('selecionado');
        indiceBusca = (indiceBusca + 1) % itens.length;
        itens[indiceBusca] && itens[indiceBusca].classList.add('selecionado');
        itens[indiceBusca] && itens[indiceBusca].scrollIntoView({{ block: 'nearest' }});
      }} else if (e.key === 'ArrowUp') {{
        e.preventDefault();
        itens[indiceBusca] && itens[indiceBusca].classList.remove('selecionado');
        indiceBusca = (indiceBusca - 1 + itens.length) % itens.length;
        itens[indiceBusca] && itens[indiceBusca].classList.add('selecionado');
        itens[indiceBusca] && itens[indiceBusca].scrollIntoView({{ block: 'nearest' }});
      }} else if (e.key === 'Enter') {{
        e.preventDefault();
        itens[indiceBusca] && itens[indiceBusca].click();
      }}
    }});
  }}
}});
</script>

<!-- MODAL DE BUSCA GLOBAL (ITEM F1.6 - CTRL+K) -->
<div id="modal-busca-global" class="busca-overlay" onclick="if(event.target===this) window.fecharModalBusca()">
  <div class="busca-modal">
    <div class="busca-campo-wrap">
      <i class="bi bi-search" style="color: var(--p-turquesa-texto, #0b7570); font-size: 16px;"></i>
      <input type="text" id="campo-busca-global" class="busca-input" placeholder="Digite para buscar abas, ferramentas ou contatos..." autocomplete="off" />
      <span style="cursor:pointer; font-size:11px; background:#f4eee7; padding:3px 8px; border-radius:6px; color:#5e5563; font-weight:700;" onclick="window.fecharModalBusca()">ESC</span>
    </div>
    <div id="busca-resultados-lista" class="busca-lista"></div>
    <div class="busca-rodape">
      <span><kbd>↑</kbd> <kbd>↓</kbd> navegar</span>
      <span><kbd>Enter</kbd> selecionar</span>
      <span><kbd>ESC</kbd> fechar</span>
    </div>
  </div>
</div>
</body></html>""")


@router.get("/v1/auth/esqueci-senha-form", include_in_schema=False)
async def tela_esqueci():
    return _pagina("Recuperar senha", """
<h1>Recuperar senha</h1>
<p>Informe seu e-mail. Se houver conta, você recebe um link para definir uma senha nova.</p>
<form id="f">
  <label for="email">E-mail</label>
  <input id="email" type="email" required autocomplete="username" value="manfredhaut@gmail.com">
  <button id="b" type="submit">Enviar link</button>
</form>
<div id="aviso" class="aviso ok"></div>
<div class="rodape"><a href="/tenant/login">Voltar ao login</a></div>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const b = document.getElementById('b'), av = document.getElementById('aviso');
  b.disabled = true; b.textContent = 'Enviando...';
  try {
    const r = await fetch('/v1/auth/esqueci-senha', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email.value})
    });
    const d = await r.json().catch(() => ({}));
    av.textContent = d.message || 'Se houver conta com esse e-mail, o link foi enviado.';
  } catch (_) {
    av.textContent = 'Se houver conta com esse e-mail, o link foi enviado.';
  }
  av.style.display = 'block'; b.textContent = 'Enviar link';
});
</script>""")


@router.post("/v1/auth/esqueci-senha", include_in_schema=False)
async def esqueci_senha(request: Request, tarefas: BackgroundTasks):
    """
    Resposta SEMPRE idêntica, exista a conta ou não.

    Uma resposta diferente para e-mail inexistente transformaria esta rota num
    verificador de quais endereços têm conta no ATENDIT — informação que vale
    para quem monta lista de phishing. O envio vai para segundo plano também
    por isso: o tempo de resposta não pode denunciar se houve envio.
    """
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}
    email = (corpo.get("email") or "").strip().lower()

    generica = {"status": "success",
                "message": "Se houver uma conta com esse e-mail, o link de recuperação foi enviado."}

    if not email:
        return JSONResponse(content=generica)

    async with AsyncSessionLocal() as sessao:
        conta = (
            await sessao.execute(select(TenantUser).where(TenantUser.email == email))
        ).scalar_one_or_none()

        if conta is None:
            logger.info(f"[RESET] Pedido para '{email}', que não tem conta. Resposta genérica enviada.")
            return JSONResponse(content=generica)

        token = secrets.token_urlsafe(32)[:64]
        conta.token_reset = token
        conta.token_reset_expira_em = datetime.now(timezone.utc) + VALIDADE_RESET
        await sessao.commit()

    tarefas.add_task(_enviar_reset, email, token)
    logger.info(f"[RESET] Token gerado para '{email}' (validade 1h).")
    return JSONResponse(content=generica)


async def _enviar_reset(email: str, token: str) -> None:
    link = f"{settings.URL_PUBLICA}/reset-senha?token={token}"
    html = montar_html(
        "Definir uma senha nova",
        ["Recebemos um pedido para redefinir a senha da sua conta no ATENDIT.",
         "O link vale por <strong>1 hora</strong> e só pode ser usado uma vez.",
         "Se não foi você que pediu, ignore esta mensagem — sua senha continua a mesma."],
        "Definir senha nova", link,
    )
    await enviar_email(email, "ATENDIT — recuperação de senha", html)


@router.get("/reset-senha", include_in_schema=False)
async def tela_reset(token: str = ""):
    if not token:
        return _pagina("Link inválido",
            "<h1>Link inválido</h1><p>Este endereço não tem um código de recuperação.</p>"
            '<div class="rodape"><a href="/tenant/login">Ir para o login</a></div>', 400)
    return _pagina("Nova senha", f"""
<h1>Definir senha nova</h1>
<p>Escolha uma senha de pelo menos 8 caracteres.</p>
<form id="f">
  <label for="s1">Nova senha</label>
  <input id="s1" type="password" required autocomplete="new-password">
  <label for="s2">Repita a senha</label>
  <input id="s2" type="password" required autocomplete="new-password">
  <button id="b" type="submit">Salvar senha</button>
</form>
<div id="aviso" class="aviso erro"></div>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const b = document.getElementById('b'), av = document.getElementById('aviso');
  av.style.display = 'none'; av.className = 'aviso erro';
  if (s1.value !== s2.value) {{
    av.textContent = 'As duas senhas não são iguais.'; av.style.display = 'block'; return;
  }}
  b.disabled = true; b.textContent = 'Salvando...';
  try {{
    const r = await fetch('/v1/auth/reset-senha', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token: '{token}', senha: s1.value}})
    }});
    const d = await r.json().catch(() => ({{}}));
    if (r.ok) {{
      av.className = 'aviso ok';
      av.textContent = 'Senha alterada. Redirecionando para o login...';
      av.style.display = 'block';
      setTimeout(() => window.location = '/tenant/login', 1600); return;
    }}
    av.textContent = d.detail || 'Não consegui alterar a senha.'; av.style.display = 'block';
  }} catch (_) {{
    av.textContent = 'Falha de conexão.'; av.style.display = 'block';
  }}
  b.disabled = false; b.textContent = 'Salvar senha';
}});
</script>""")


@router.post("/v1/auth/reset-senha", include_in_schema=False)
async def gravar_senha_nova(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}
    token = (corpo.get("token") or "").strip()
    senha = corpo.get("senha") or ""

    if len(senha) < 8:
        return JSONResponse(status_code=400, content={"detail": "A senha precisa de pelo menos 8 caracteres."})
    if not token:
        return JSONResponse(status_code=400, content={"detail": "Link inválido."})

    async with AsyncSessionLocal() as sessao:
        conta = (
            await sessao.execute(select(TenantUser).where(TenantUser.token_reset == token))
        ).scalar_one_or_none()

        agora = datetime.now(timezone.utc)
        if conta is None:
            return JSONResponse(status_code=400, content={"detail": "Link inválido ou já utilizado."})

        vence = conta.token_reset_expira_em
        if vence is not None and vence.tzinfo is None:
            vence = vence.replace(tzinfo=timezone.utc)
        if vence is None or vence < agora:
            # Limpa o token vencido para nao deixar lixo utilizavel no banco.
            conta.token_reset = None
            conta.token_reset_expira_em = None
            await sessao.commit()
            return JSONResponse(status_code=400, content={"detail": "Este link expirou. Peça outro."})

        conta.password_hash = _sessao.gerar_hash(senha)
        conta.token_reset = None                 # uso unico
        conta.token_reset_expira_em = None
        # Quem recupera a senha por e-mail provou que controla o endereco, entao
        # aproveitamos para confirma-lo: caso contrario a conta trocaria de senha
        # e continuaria barrada no login por falta de verificacao.
        conta.email_verificado = True
        await sessao.commit()
        endereco = conta.email

    logger.info(f"[RESET] Senha redefinida para '{endereco}'. Token invalidado.")
    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# F1.4 - Modelos por Segmento
# ---------------------------------------------------------------------------
@router.get("/api/templates/segmentos")
async def api_listar_templates_segmentos():
    from app.services.segment_templates import listar_templates
    return JSONResponse(content={"ok": True, "templates": listar_templates()})

@router.post("/api/templates/aplicar")
async def api_aplicar_template_segmento(request: Request):
    from app.services.segment_templates import obter_template
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    import json
    
    corpo = await request.json()
    template_id = corpo.get("template_id")
    slug = corpo.get("slug", "conta")
    
    template = obter_template(template_id)
    if not template:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Template inválido."})
        
    async with AsyncSessionLocal() as session:
        # Atualiza a persona e o prompt na tabela tenants (meta_data ou ai_config)
        await session.execute(text("""
            UPDATE tenants 
            SET meta_data = COALESCE(meta_data, '{}'::jsonb) || :dados
            WHERE slug = :slug
        """), {
            "slug": slug,
            "dados": json.dumps({
                "segmento": template_id,
                "prompt_ia": template["prompt_ia"],
                "perguntas_qualificacao": template["perguntas_qualificacao"],
                "mensagem_boas_vindas": template["mensagem_boas_vindas"],
                "etapas_funil": template["etapas_funil"]
            })
        })
        await session.commit()
        
    return JSONResponse(content={
        "ok": True, 
        "mensagem": f"Modelo '{template['nome']}' aplicado com sucesso!",
        "template": template
    })



# ---------------------------------------------------------------------------
# F1.6 - Busca Global e Atalhos (Ctrl+K)
# ---------------------------------------------------------------------------
@router.get("/api/busca-global")
async def api_busca_global(request: Request, q: str = ""):
    from fastapi.responses import JSONResponse
    termo = q.strip().lower()
    if len(termo) == 0:
        return JSONResponse(content={"ok": True, "resultados": []})

    resultados = []

    modulos = [
        {"tipo": "Aba", "titulo": "1. Início", "subtitulo": "Painel Principal e visão geral", "acao": "inicio", "icone": "🏠", "tags": "inicio home dashboard metricas atencao"},
        {"tipo": "Aba", "titulo": "2. Atendimento", "subtitulo": "Fila de conversas em tempo real", "acao": "fila_atendimento", "icone": "💬", "tags": "chat atendimento fila conversas operador"},
        {"tipo": "Aba", "titulo": "3. Aquisição", "subtitulo": "Captação de leads omnichannel", "acao": "leads", "icone": "🎯", "tags": "aquisicao leads captacao formularios"},
        {"tipo": "Aba", "titulo": "4. CRM & Funil", "subtitulo": "Pipeline e estágios de conversão", "acao": "leads", "icone": "📊", "tags": "crm funil vendas pipeline negociacao"},
        {"tipo": "Aba", "titulo": "5. Agenda", "subtitulo": "Calendly, Google Calendar e horários", "acao": "calendar_config", "icone": "📅", "tags": "agenda calendario calendly google horarios"},
        {"tipo": "Aba", "titulo": "6. Profissionais & Escalas", "subtitulo": "Equipe, turnos e permissões", "acao": "equipe", "icone": "👥", "tags": "profissionais equipe membros escalas permissoes"},
        {"tipo": "Aba", "titulo": "7. Vitrine & Loja", "subtitulo": "Catálogo de produtos e e-commerce", "acao": "ecommerce_config", "icone": "🛍️", "tags": "vitrine loja ecommerce produtos catalogo"},
        {"tipo": "Aba", "titulo": "8. Consultoria por Vídeo", "subtitulo": "Salas WebRTC e atendimento ao vivo", "acao": "video", "icone": "📹", "tags": "video webrtc livekit salas consultoria reuniao"},
        {"tipo": "Aba", "titulo": "9. Assistente IA", "subtitulo": "Personalidade e regras do atendente", "acao": "ia_config", "icone": "🤖", "tags": "ia assistente bot inteligencia atendente"},
        {"tipo": "Aba", "titulo": "10. Base de Conhecimento", "subtitulo": "Documentos RAG e manuais", "acao": "rag_management", "icone": "📚", "tags": "base conhecimento rag documentos pdf manuais"},
        {"tipo": "Aba", "titulo": "11. Canais", "subtitulo": "WhatsApp Oficial e QR Code", "acao": "canais", "icone": "📡", "tags": "canais whatsapp meta oficial qr code evolution"},
        {"tipo": "Aba", "titulo": "12. Financeiro", "subtitulo": "Faturas, planos e consumo de IA", "acao": "faturamento", "icone": "💳", "tags": "financeiro faturamento faturas planos consumo"},
        {"tipo": "Aba", "titulo": "13. Inteligência Operacional", "subtitulo": "Analytics, relatórios e KPIs", "acao": "intel_operacional", "icone": "📈", "tags": "inteligencia operacional relatorios analytics kpis"},
        {"tipo": "Aba", "titulo": "14. Empresa & Conta", "subtitulo": "Cadastro, dados da empresa e segurança", "acao": "empresa_cadastro", "icone": "🏢", "tags": "empresa conta perfil cadastro dados senha"},
        {"tipo": "Ação Rápida", "titulo": "Conectar WhatsApp QR Code", "subtitulo": "Escanear QR Code com o aplicativo", "acao": "canais", "subaba": "qrcode", "icone": "📲", "tags": "conectar qrcode evolution celular"},
        {"tipo": "Ação Rápida", "titulo": "Configurar Meta Oficial (WABA)", "subtitulo": "Credenciais Cloud API Oficial", "acao": "canais", "subaba": "meta", "icone": "🌐", "tags": "meta oficial waba cloud api token"},
        {"tipo": "Ação Rápida", "titulo": "Nova Sala de Vídeo", "subtitulo": "Gerar link instantâneo de videoconferência", "acao": "video", "icone": "➕", "tags": "criar sala video chamada link"}
    ]

    for m in modulos:
        if termo in m["titulo"].lower() or termo in m["subtitulo"].lower() or termo in m.get("tags", "").lower():
            resultados.append(m)

    dados = _sessao.sessao_do_tenant(request)
    if dados and "tenant_id" in dados:
        try:
            from app.core.database import AsyncSessionLocal
            from sqlalchemy import text
            import uuid
            t_uuid = uuid.UUID(str(dados["tenant_id"]))
            async with AsyncSessionLocal() as session:
                q_lead = await session.execute(text("""
                    SELECT id, nome, whatsapp, email, status
                    FROM leads
                    WHERE tenant_id = :t_id
                      AND (nome ILIKE :t OR whatsapp ILIKE :t OR email ILIKE :t)
                    ORDER BY created_at DESC LIMIT 5
                """), {"t_id": t_uuid, "t": f"%{termo}%"})
                for row in q_lead.fetchall():
                    resultados.append({
                        "tipo": "Lead",
                        "titulo": row[1] or "Lead",
                        "subtitulo": f"Status: {(row[4] or 'novo').capitalize()} · {row[2] or row[3] or ''}",
                        "acao": "leads",
                        "icone": "👤"
                    })
        except Exception:
            pass

    return JSONResponse(content={"ok": True, "resultados": resultados[:10]})



# ---------------------------------------------------------------------------
# F1.7 - Endpoints de Equipe e Permissões
# ---------------------------------------------------------------------------
@router.get("/api/meu-perfil")
async def api_meu_perfil(request: Request):
    from app.core import autorizacao as _autz
    perfil = await _autz.obter_perfil_sessao(request)
    return JSONResponse(content={"ok": True, "perfil": perfil})

@router.get("/api/equipe")
async def api_listar_equipe(request: Request):
    from app.core import autorizacao as _autz
    perfil = await _autz.obter_perfil_sessao(request)
    if perfil["role"] not in ("dono", "gestor") and perfil["tipo"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "mensagem": "Acesso restrito a Donos e Gestores."})

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant_user import TenantUser

    async with AsyncSessionLocal() as session:
        # Pega membros do tenant
        sessao_t = _autz.sessao_tenant(request)
        tenant_id = sessao_t["tenant_id"] if sessao_t else None

        query = select(TenantUser)
        if tenant_id:
            query = query.where(TenantUser.tenant_id == tenant_id)
        query = query.order_by(TenantUser.created_at.asc())

        res = await session.execute(query)
        usuarios = res.scalars().all()

        membros = []
        for u in usuarios:
            membros.append({
                "id": str(u.id),
                "email": u.email,
                "role": u.role or "dono",
                "is_active": u.is_active,
                "criado_em": u.created_at.strftime("%d/%m/%Y") if u.created_at else "—",
                "ultimo_acesso": u.last_login_at.strftime("%d/%m/%Y %H:%M") if u.last_login_at else "Nunca"
            })

        return JSONResponse(content={"ok": True, "membros": membros, "meu_papel": perfil["role"]})

@router.post("/api/equipe/convidar")
async def api_convidar_membro(request: Request):
    from app.core import autorizacao as _autz
    perfil = await _autz.obter_perfil_sessao(request)
    if perfil["role"] not in ("dono", "gestor") and perfil["tipo"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "mensagem": "Apenas Donos ou Gestores podem convidar membros."})

    corpo = await request.json()
    email = (corpo.get("email") or "").strip().lower()
    senha = corpo.get("senha") or "Mudar@1234"
    role = corpo.get("role") or "operador"

    if not email or "@" not in email:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "E-mail inválido."})

    if role not in _autz.PAPEIS_VALIDOS:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Papel inválido."})

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant_user import TenantUser
    from app.models.tenant import Tenant
    from app.core.panel_auth import gerar_hash

    async with AsyncSessionLocal() as session:
        # Verificar se já existe o e-mail
        res_exist = await session.execute(select(TenantUser).where(TenantUser.email == email))
        if res_exist.scalar_one_or_none():
            return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Este e-mail já está cadastrado."})

        # Obter tenant_id
        sessao_t = _autz.sessao_tenant(request)
        tenant_id = sessao_t["tenant_id"] if sessao_t else None
        if not tenant_id:
            res_t = await session.execute(select(Tenant.id).limit(1))
            tenant_id = res_t.scalar_one()

        novo_usuario = TenantUser(
            tenant_id=tenant_id,
            email=email,
            password_hash=gerar_hash(senha),
            role=role,
            is_active=True,
            email_verificado=True
        )
        session.add(novo_usuario)
        await session.commit()

        return JSONResponse(content={"ok": True, "mensagem": f"Membro {email} cadastrado com sucesso como {role.capitalize()}!"})

@router.put("/api/equipe/{usuario_id}/role")
async def api_alterar_role_membro(usuario_id: str, request: Request):
    from app.core import autorizacao as _autz
    perfil = await _autz.obter_perfil_sessao(request)
    if perfil["role"] != "dono" and perfil["tipo"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "mensagem": "Apenas o Dono pode alterar papéis."})

    import uuid
    try:
        uid = uuid.UUID(usuario_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de usuário inválido."})

    corpo = await request.json()
    novo_role = corpo.get("role")
    if novo_role not in _autz.PAPEIS_VALIDOS:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Papel inválido."})

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant_user import TenantUser

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(TenantUser).where(TenantUser.id == uid))
        usuario = res.scalar_one_or_none()
        if not usuario:
            return JSONResponse(status_code=404, content={"ok": False, "mensagem": "Membro não encontrado."})

        usuario.role = novo_role
        await session.commit()
        return JSONResponse(content={"ok": True, "mensagem": f"Papel atualizado para {novo_role.capitalize()} com sucesso!"})

@router.delete("/api/equipe/{usuario_id}")
async def api_remover_membro(usuario_id: str, request: Request):
    from app.core import autorizacao as _autz
    perfil = await _autz.obter_perfil_sessao(request)
    if perfil["role"] != "dono" and perfil["tipo"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "mensagem": "Apenas o Dono pode remover membros."})

    import uuid
    try:
        uid = uuid.UUID(usuario_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de usuário inválido."})

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant_user import TenantUser

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(TenantUser).where(TenantUser.id == uid))
        usuario = res.scalar_one_or_none()
        if not usuario:
            return JSONResponse(status_code=404, content={"ok": False, "mensagem": "Membro não encontrado."})

        sessao_t = _autz.sessao_tenant(request)
        if sessao_t and sessao_t.get("usuario_id") == uid:
            return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Você não pode excluir seu próprio usuário."})

        await session.delete(usuario)
        await session.commit()
        return JSONResponse(content={"ok": True, "mensagem": "Membro removido da equipe com sucesso."})




# ---------------------------------------------------------------------------
# F2.1 - Formulários de Captura e Endpoints Públicos de Leads
# ---------------------------------------------------------------------------
@router.post("/api/public/{slug}/leads")
async def api_public_captura_lead(slug: str, request: Request, tarefas: BackgroundTasks):
    """
    Endpoint público com CORS aberto para receber leads de formulários externos,
    landing pages e integrações de marketing vinculados ao slug do cliente.
    """
    from fastapi.responses import JSONResponse
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.tenant import Tenant
    from app.models.scheduling import Lead

    try:
        corpo = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Payload JSON inválido."})

    nome = (corpo.get("nome") or "").strip()
    if not nome:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "O campo 'nome' é obrigatório."})

    whatsapp_bruto = (corpo.get("whatsapp") or corpo.get("telefone") or "").strip()
    # Sanitização: apenas dígitos
    whatsapp_limpo = "".join([c for c in whatsapp_bruto if c.isdigit()])
    if whatsapp_limpo and len(whatsapp_limpo) < 10:
        whatsapp_limpo = None

    email = (corpo.get("email") or "").strip().lower() or None
    comentario = (corpo.get("comentario") or corpo.get("mensagem") or "").strip() or None
    origem = (corpo.get("origem") or "formulario_web").strip()[:40]

    utm_source = (corpo.get("utm_source") or "").strip()[:100] or None
    utm_medium = (corpo.get("utm_medium") or "").strip()[:100] or None
    utm_campaign = (corpo.get("utm_campaign") or "").strip()[:100] or None

    async with AsyncSessionLocal() as session:
        # Localiza o tenant pelo slug
        res = await session.execute(select(Tenant).where(Tenant.slug == slug))
        inquilino = res.scalar_one_or_none()
        if not inquilino:
            return JSONResponse(status_code=404, content={"ok": False, "mensagem": f"Inquilino '{slug}' não encontrado."})

        novo_lead = Lead(
            tenant_id=inquilino.id,
            nome=nome[:150],
            whatsapp=whatsapp_limpo[:32] if whatsapp_limpo else None,
            email=email[:255] if email else None,
            comentario=comentario,
            origem=origem,
            status="novo",
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign
        )
        session.add(novo_lead)
        await session.commit()
        await session.refresh(novo_lead)
        lead_id = str(novo_lead.id)

    # Dispara a qualificação por IA em background sem reter a resposta HTTP
    from app.services.lead_qualification_service import qualificar_lead_ia
    tarefas.add_task(qualificar_lead_ia, novo_lead.id)

    # Dispara mensagem automática de boas-vindas no WhatsApp do lead (F2.3)
    from app.services.lead_welcome_service import enviar_boas_vindas_lead
    tarefas.add_task(enviar_boas_vindas_lead, novo_lead.id)

    return JSONResponse(status_code=201, content={
        "ok": True,
        "mensagem": "Lead capturado com sucesso!",
        "lead_id": lead_id
    })

@router.get("/api/tenant/leads")
async def api_listar_leads_tenant(request: Request, status_filtro: Optional[str] = None):
    """Listagem de leads filtrada por tenant."""
    from fastapi.responses import JSONResponse
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select, desc
    from app.models.scheduling import Lead

    sessao_t = _autz.sessao_tenant(request)
    sessao_adm = _autz.sessao_admin(request)
    if not sessao_t and not sessao_adm:
        return JSONResponse(status_code=401, content={"ok": False, "mensagem": "Não autenticado."})

    tenant_id = sessao_t.get("tenant_id") if sessao_t else None

    async with AsyncSessionLocal() as session:
        query = select(Lead)
        if tenant_id:
            query = query.where(Lead.tenant_id == tenant_id)
        if status_filtro and status_filtro != "todos":
            query = query.where(Lead.status == status_filtro)

        query = query.order_by(desc(Lead.created_at)).limit(100)
        res = await session.execute(query)
        leads = res.scalars().all()

        lista = []
        for l in leads:
            lista.append({
                "id": str(l.id),
                "nome": l.nome,
                "whatsapp": l.whatsapp or "—",
                "email": l.email or "—",
                "comentario": l.comentario or "—",
                "origem": l.origem,
                "status": l.status or "novo",
                "temperatura": getattr(l, "temperatura", "morno"),
                "score": getattr(l, "score", 50),
                "intencao": getattr(l, "intencao", None) or "—",
                "resumo_ia": getattr(l, "resumo_ia", None) or "Sem análise de IA",
                "prioridade": getattr(l, "prioridade", "media"),
                "boas_vindas_enviada": getattr(l, "boas_vindas_enviada", False),
                "alerta_equipe_enviado": getattr(l, "alerta_equipe_enviado", False),
                "alerta_equipe_enviado_em": l.alerta_equipe_enviado_em.strftime("%d/%m/%Y %H:%M") if getattr(l, "alerta_equipe_enviado_em", None) else None,
                "boas_vindas_enviada_em": l.boas_vindas_enviada_em.strftime("%d/%m/%Y %H:%M") if getattr(l, "boas_vindas_enviada_em", None) else None,
                "utm_source": l.utm_source or "—",
                "utm_medium": l.utm_medium or "—",
                "utm_campaign": l.utm_campaign or "—",
                "criado_em": l.created_at.strftime("%d/%m/%Y %H:%M") if l.created_at else "—"
            })

        return JSONResponse(content={"ok": True, "leads": lista})

@router.patch("/api/tenant/leads/{lead_id}/status")
async def api_atualizar_status_lead(lead_id: str, request: Request):
    """Atualiza a fase do lead no funil."""
    from fastapi.responses import JSONResponse
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.scheduling import Lead
    import uuid

    if not _autz.tem_alguma_sessao(request):
        return JSONResponse(status_code=401, content={"ok": False, "mensagem": "Não autenticado."})

    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de lead inválido."})

    corpo = await request.json()
    novo_status = corpo.get("status")
    if novo_status not in ("novo", "qualificado", "em_atendimento", "convertido", "arquivado"):
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "Status inválido."})

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Lead).where(Lead.id == lid))
        lead = res.scalar_one_or_none()
        if not lead:
            return JSONResponse(status_code=404, content={"ok": False, "mensagem": "Lead não encontrado."})

        lead.status = novo_status
        await session.commit()
        return JSONResponse(content={"ok": True, "mensagem": f"Status atualizado para {novo_status}!"})




@router.post("/api/tenant/leads/{lead_id}/qualificar")
async def api_requalificar_lead(lead_id: str, request: Request, tarefas: BackgroundTasks):
    """Aciona re-qualificação imediata com IA."""
    from fastapi.responses import JSONResponse
    from app.core import autorizacao as _autz
    from app.services.lead_qualification_service import qualificar_lead_ia
    import uuid

    if not _autz.tem_alguma_sessao(request):
        return JSONResponse(status_code=401, content={"ok": False, "mensagem": "Não autenticado."})

    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de lead inválido."})

    # Executa a análise de IA
    resultado = await qualificar_lead_ia(lid)
    if resultado:
        return JSONResponse(content={"ok": True, "mensagem": "Lead qualificado pela IA com sucesso!", "dados": resultado})
    return JSONResponse(status_code=500, content={"ok": False, "mensagem": "Falha na análise da IA."})




@router.post("/api/tenant/leads/{lead_id}/boas-vindas")
async def api_reenviar_boas_vindas(lead_id: str, request: Request, tarefas: BackgroundTasks):
    """Dispara ou reenvia mensagem de boas-vindas no WhatsApp do lead."""
    from fastapi.responses import JSONResponse
    from app.core import autorizacao as _autz
    from app.services.lead_welcome_service import enviar_boas_vindas_lead
    import uuid

    if not _autz.tem_alguma_sessao(request):
        return JSONResponse(status_code=401, content={"ok": False, "mensagem": "Não autenticado."})

    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de lead inválido."})

    # Força envio imediato
    enviado = await enviar_boas_vindas_lead(lid)
    if enviado:
        return JSONResponse(content={"ok": True, "mensagem": "Mensagem de boas-vindas entregue ao WhatsApp!"})
    return JSONResponse(status_code=500, content={"ok": False, "mensagem": "Não foi possível entregar a mensagem (verifique se a instância está conectada)."})




@router.post("/api/tenant/leads/{lead_id}/alerta-equipe")
async def api_disparar_alerta_equipe(lead_id: str, request: Request):
    """Dispara ou reenvia notificação interna para o WhatsApp da equipe sobre o lead."""
    from fastapi.responses import JSONResponse
    from app.core import autorizacao as _autz
    from app.services.lead_alert_service import enviar_alerta_lead_quente
    import uuid

    if not _autz.tem_alguma_sessao(request):
        return JSONResponse(status_code=401, content={"ok": False, "mensagem": "Não autenticado."})

    try:
        lid = uuid.UUID(lead_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "mensagem": "ID de lead inválido."})

    enviado = await enviar_alerta_lead_quente(lid)
    if enviado:
        return JSONResponse(content={"ok": True, "mensagem": "Alerta interno entregue ao WhatsApp da equipe!"})
    return JSONResponse(status_code=500, content={"ok": False, "mensagem": "Não foi possível entregar o alerta à equipe (verifique conexão ou dados do lead)."})



# ---------------------------------------------------------------------------
# ALTERAÇÃO DE SENHA POR USUÁRIO AUTENTICADO NO PAINEL
# ---------------------------------------------------------------------------
from pydantic import BaseModel

class AlterarSenhaRequest(BaseModel):
    senha_atual: str
    nova_senha: str


@router.post("/v1/auth/alterar-senha", include_in_schema=False)
async def alterar_senha_autenticado(request: Request, payload: AlterarSenhaRequest):
    """Permite que o usuário autenticado altere sua própria senha."""
    from app.core import panel_auth, tenant_auth
    from app.models.admin import AdminUser

    # 1. Verifica se é sessão Admin
    if panel_auth.sessao_ativa(request):
        if len(payload.nova_senha) < 8:
            raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 8 caracteres.")
        
        async with AsyncSessionLocal() as sessao:
            admin = (await sessao.execute(select(AdminUser).where(AdminUser.username == "admin"))).scalar_one_or_none()
            if not admin:
                raise HTTPException(status_code=404, detail="Administrador não encontrado.")
            
            if not panel_auth.conferir_hash(payload.senha_atual, admin.password_hash):
                raise HTTPException(status_code=401, detail="Senha atual incorreta.")
            
            admin.password_hash = panel_auth.gerar_hash(payload.nova_senha)
            await sessao.commit()
            logger.info("[AUTH] Senha do administrador alterada com sucesso via painel.")
            return {"status": "ok", "message": "Senha de administrador alterada com sucesso."}

    # 2. Verifica se é sessão de Tenant
    sessao_t = tenant_auth.sessao_do_tenant(request)
    if sessao_t:
        if len(payload.nova_senha) < 8:
            raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 8 caracteres.")
        
        async with AsyncSessionLocal() as sessao:
            user = await sessao.get(TenantUser, uuid.UUID(sessao_t["usuario_id"]))
            if not user:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")
            
            if not panel_auth.conferir_hash(payload.senha_atual, user.password_hash):
                raise HTTPException(status_code=401, detail="Senha atual incorreta.")
            
            user.password_hash = panel_auth.gerar_hash(payload.nova_senha)
            await sessao.commit()
            logger.info(f"[AUTH] Senha do usuário '{user.email}' alterada com sucesso via painel.")
            return {"status": "ok", "message": "Sua senha foi alterada com sucesso."}

    raise HTTPException(status_code=401, detail="Sessão não autenticada.")
