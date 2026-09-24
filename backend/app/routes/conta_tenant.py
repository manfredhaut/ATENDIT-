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

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core import tenant_auth as _sessao
from app.core.config import settings
from app.core.database import AsyncSessionLocal
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
    """
    Painel do cliente — reaproveita as MESMAS telas de /dashboards/* que o
    painel admin usa. Nenhuma view foi recriada.

    > ### 🔴 O SLUG VEM DA SESSÃO, NUNCA DA URL
    > `slug` é lido do cookie de sessão e injetado no HTML pelo servidor. Não
    > existe `?slug=` nem `/tenant/painel/{slug}` — se existisse, trocar uma
    > palavra na barra de endereço apontaria o painel para outro inquilino.
    >
    > Isso é **defesa em profundidade, não a defesa principal**: mesmo que
    > alguém forje o slug no JavaScript, as rotas `/v1/*` conferem o inquilino
    > da sessão em `autorizacao.exigir_acesso_ao_tenant` e devolvem 403.
    > As duas camadas existem porque a de baixo é a que realmente protege, e a
    > de cima evita que o painel sequer tente algo inválido.
    """
    dados = _sessao.sessao_do_tenant(request)
    if not dados:      # cinto e suspensorio: o middleware ja barra
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

    slug = inquilino.slug
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
  .lateral{{width:260px;background:var(--p-berinjela, #231726);color:#DCD2DF;display:flex;flex-direction:column;
           flex-shrink:0}}
  .marca{{padding:22px 20px;font-family:var(--p-fonte-titulo, Georgia, serif);font-size:22px;font-weight:400;color:var(--p-turquesa, #3ccbc5);letter-spacing:.5px;
         border-bottom:1px solid rgba(255,255,255,0.08)}}
  .menu{{padding:14px 0;flex:1}}
  .item{{display:block;padding:11px 20px;font-size:13.5px;color:#B5A8BA;cursor:pointer;
        border-left:3px solid transparent;transition:all 0.15s ease}}
  .item:hover{{background:rgba(255,255,255,0.06);color:#ffffff}}
  .item.ativo{{background:#3A2740;color:var(--p-turquesa, #3ccbc5);border-left-color:var(--p-turquesa, #3ccbc5);font-weight:600}}
  .rodape{{padding:16px 20px;border-top:1px solid rgba(255,255,255,0.08)}}
  .rodape a{{color:#B5A8BA;font-size:13px;text-decoration:none;transition:color 0.15s ease}}
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
  .aviso{{background:#fff;border:1px solid var(--p-borda, #eae3dc);border-radius:var(--p-raio, 16px);padding:26px;
         max-width:720px;box-shadow:0 1px 3px rgba(0,0,0,0.02)}}
  .aviso h2{{margin:0 0 12px;font-family:var(--p-fonte-titulo, Georgia, serif);font-size:18px}}
  .aviso p{{margin:0 0 12px;font-size:14px;line-height:1.6;color:var(--p-texto-suave, #5e5563)}}
  .aviso a{{color:var(--p-turquesa-texto, #0b7570);font-weight:600}}
</style></head><body>
<div class="lateral">
  <div class="marca">PRESENTHIA</div>
  <nav class="menu" id="menu">
    <a class="item ativo" data-v="inicio">📊 Painel Principal</a>
    <a class="item" data-v="empresa_cadastro">🏢 Cadastro da Empresa</a>
    <a class="item" data-v="ia_config">⚙️ Configurações do Atendente</a>
    <a class="item" data-v="link_whatsapp">📲 Conexão WhatsApp QR Code</a>
    <a class="item" data-v="meta_config">🌐 WhatsApp Meta Oficial</a>
    <a class="item" data-v="calendar_config">📅 Configuração do Calendário</a>
    <a class="item" data-v="rag_management">📁 Gestão de Documentos</a>
    <a class="item" data-v="fila_atendimento">💬 Atendimento</a>
    <a class="item" data-v="gemini_config">🧠 Configuração da IA</a>
    <a class="item" data-v="ecommerce_config">🛒 Loja & E-Commerce</a>
    <a class="item" data-v="intel_operacional">📊 Inteligência Comercial & Operacional</a>
  </nav>
  <div class="rodape"><a href="/tenant/logout">← Sair da conta</a></div>
</div>
<div class="principal">
  <header class="topo">
    <h1 id="titulo">{inquilino.name}</h1>
    <div style="display:flex;align-items:center;gap:12px">
      <span class="cracha">{slug}</span>
      <span style="font-size:12.5px;color:#6b7280">{conta.email}</span>
      <a class="sair" href="/tenant/logout">Sair</a>
    </div>
  </header>
  <div class="area" id="conteudo">
    <div class="aviso">
      <h2>Bem-vindo, {inquilino.name}</h2>
      <p>Escolha uma opção no menu à esquerda. Você está vendo <strong>apenas os
         dados da sua empresa</strong> — o identificador
         <code>{slug}</code> vem da sua sessão e não pode ser trocado pela URL.</p>
      <p><strong>WhatsApp e agenda começam desconectados.</strong> Conectar é
         passo seu: use <em>Conexão WhatsApp</em> e
         <em>Configuração do Calendário</em>.</p>
      <p>Conectar agenda agora:
        <a href="/calendar/oauth/google/start?tenant={slug}">Google Calendar</a> ·
        <a href="/calendar/oauth/microsoft/start?tenant={slug}">Microsoft 365</a></p>
    </div>
  </div>
</div>
<script>
// O slug vem do SERVIDOR, a partir do cookie de sessao. As telas de
// /dashboards/* leem window.currentTenantSlug -- e por isso que elas
// funcionam aqui sem nenhuma alteracao.
window.currentTenantSlug = {slug!r};
window.currentTenantData = {{ id: {str(inquilino.id)!r}, slug: window.currentTenantSlug, name: {inquilino.name!r} }};

const htmlInicio = `<div class="p-card" style="padding: 32px; max-width: 820px; background: #ffffff; border: 1px solid var(--p-borda, #eae3dc); border-radius: var(--p-raio, 16px); box-shadow: 0 2px 6px rgba(0,0,0,0.02);">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--p-borda, #eae3dc); padding-bottom: 18px; margin-bottom: 22px;">
      <div>
        <h2 class="p-titulo" style="font-size: 1.6rem; color: var(--p-texto, #231726); margin: 0 0 4px 0;">Bem-vindo, {inquilino.name}</h2>
        <p style="font-size: 0.9rem; color: var(--p-texto-suave, #5e5563); margin: 0;">Painel de controle unificado Presenthia para atendimento inteligente e gestão multicanal.</p>
      </div>
      <span class="chip p-chip-ok" style="font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 999px;">
        ID: {slug}
      </span>
    </div>

    <div style="font-size: 0.92rem; line-height: 1.65; color: var(--p-texto, #231726); display: flex; flex-direction: column; gap: 16px;">
      <p style="margin: 0;">
        Você está gerenciando o ambiente exclusivo da sua empresa. Todas as mensagens, atendimentos, regras de inteligência e históricos estão restritos à sua conta com isolamento estrito de dados.
      </p>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 8px 0;">
        <div style="padding: 18px; background: var(--p-marfim, #fbf8f4); border: 1px solid var(--p-borda, #eae3dc); border-radius: 12px;">
          <div style="font-weight: 700; font-size: 0.92rem; color: var(--p-texto, #231726); margin-bottom: 6px;">📱 Canais de WhatsApp</div>
          <p style="font-size: 0.82rem; color: var(--p-texto-suave, #5e5563); margin: 0 0 12px 0;">Conecte via Evolution API (QR Code) ou configure a API Oficial da Meta com faturamento direto.</p>
          <div style="display: flex; gap: 10px;">
            <a href="javascript:void(0)" onclick="document.querySelector('[data-v=link_whatsapp]').click()" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Conectar QR Code ↗</a>
            <a href="javascript:void(0)" onclick="document.querySelector('[data-v=meta_config]').click()" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Configurar Meta ↗</a>
          </div>
        </div>

        <div style="padding: 18px; background: var(--p-marfim, #fbf8f4); border: 1px solid var(--p-borda, #eae3dc); border-radius: 12px;">
          <div style="font-weight: 700; font-size: 0.92rem; color: var(--p-texto, #231726); margin-bottom: 6px;">📅 Agendamento Integrado</div>
          <p style="font-size: 0.82rem; color: var(--p-texto-suave, #5e5563); margin: 0 0 12px 0;">Sincronize com agendas para que o atendente IA consulte horários livres e marque compromissos.</p>
          <div style="display: flex; gap: 12px;">
            <a href="/calendar/oauth/google/start?tenant={slug}" target="_blank" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Google Calendar ↗</a>
            <a href="/calendar/oauth/microsoft/start?tenant={slug}" target="_blank" style="font-size: 0.82rem; font-weight: 700; color: var(--p-turquesa-texto, #0b7570); text-decoration: underline;">Microsoft 365 ↗</a>
          </div>
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

  area.innerHTML = '<p style="color:#6b7280">Carregando…</p>';
  try {{
    const r = await fetch('/dashboards/' + nome);
    if (r.status === 401 || r.status === 403) {{ window.location = '/tenant/login'; return; }}
    // A rota devolve o HTML EMBRULHADO EM JSON (JSONResponse(f.read())).
    // Nao e um bug daqui: e o contrato existente, o mesmo que o index.html
    // consome. Desembrulhar aqui evita mexer numa rota que o painel admin usa.
    const html = await r.json();
    area.innerHTML = html;
    // innerHTML NAO executa <script>. Sem recriar as tags, as telas
    // aparecem mas nao funcionam -- botao que nao faz nada.
    area.querySelectorAll('script').forEach(antigo => {{
      const novo = document.createElement('script');
      if (antigo.src) novo.src = antigo.src; else novo.textContent = antigo.textContent;
      antigo.replaceWith(novo);
    }});
    document.getElementById('titulo').textContent =
      (el ? el.textContent.replace(/^[^A-Za-zÀ-ÿ]+/, '').trim() : {inquilino.name!r});
  }} catch (e) {{
    area.innerHTML = '<div class="aviso"><h2>Não consegui abrir esta tela</h2>'
      + '<p>Tente de novo em instantes.</p></div>';
  }}
}}

document.getElementById('menu').addEventListener('click', (ev) => {{
  const item = ev.target.closest('.item');
  if (item) carregarView(item.dataset.v, item);
}});
</script>
</body></html>""")


# ---------------------------------------------------------------------------
# esqueci minha senha
# ---------------------------------------------------------------------------
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
