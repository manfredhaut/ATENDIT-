import asyncio
import os
import json
import re
import urllib.request
import urllib.parse
import urllib.error
import logging
import time
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, Request, HTTPException, Response, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("atendit-api")



app = FastAPI(title="ATENDIT SaaS Engine", version="1.0.0")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    import base64
    ico = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    return Response(content=ico, media_type="image/x-icon", status_code=200)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.core.config import settings
from app.core import panel_auth as _painel


def _recusar_sem_sessao(request: Request, destino_login: str):
    """
    Recusa uma requisicao sem sessao.

    Navegador (Accept: text/html) e redirecionado para o login, levando o
    caminho original em `next` para voltar depois. Chamada de fetch()/XHR leva
    401 JSON: redirecionar um XHR faria o painel injetar o HTML do login dentro
    da area de conteudo em vez de avisar o erro.
    """
    aceita = (request.headers.get("accept") or "").lower()
    if "text/html" in aceita:
        alvo = request.url.path
        if request.url.query:
            alvo += "?" + request.url.query
        return RedirectResponse(
            f"{destino_login}?next={urllib.parse.quote(alvo, safe='/?=&')}",
            status_code=303,
        )
    return JSONResponse(status_code=401, content={"detail": "Sessão ausente ou expirada. Faça login."})


@app.middleware("http")
async def exigir_sessao_do_painel(request: Request, call_next):
    """
    Exige sessao APENAS nos caminhos de _painel.CAMINHOS_PROTEGIDOS.

    Lista por excecao: tudo o que nao esta la passa. Ver o comentario em
    app/core/panel_auth.py sobre por que o inverso e perigoso.
    """
    caminho = request.url.path

    # Duas listas, com regras diferentes:
    #   somente_admin        -> exige sessao de ADMIN. Sessao de tenant NAO passa.
    #   caminho_protegido    -> aceita admin OU tenant; a rota confere o inquilino.
    if _painel.somente_admin(caminho):
        if _painel.sessao_ativa(request):
            return await call_next(request)
        return _recusar_sem_sessao(request, "/login")

    if not _painel.caminho_protegido(caminho):
        return await call_next(request)

    # Desde 2026-09-01 a sessao de TENANT tambem passa por aqui. O middleware
    # so verifica que EXISTE sessao; a conferencia de QUAL inquilino e feita
    # dentro de cada rota, com autorizacao.exigir_acesso_ao_tenant(), porque so
    # a rota sabe de onde vem o alvo (corpo do POST, path, etc.).
    #
    # 🔴 Consequencia: rota protegida que manipula dado de inquilino e NAO
    # chama exigir_acesso_ao_tenant fica aberta a qualquer tenant logado.
    # A lista de quem chama esta em RESTAURAR-AUTH.md.
    from app.core import autorizacao as _autz

    if _autz.tem_alguma_sessao(request):
        return await call_next(request)

    # Navegacao do navegador leva 'text/html' no Accept; fetch() manda '*/*'.
    # Redirecionar um XHR para a pagina de login faria o painel injetar o
    # HTML do login dentro da area de conteudo, em vez de avisar o erro.
    aceita = (request.headers.get("accept") or "").lower()
    if "text/html" in aceita:
        destino = request.url.path
        if request.url.query:
            destino += "?" + request.url.query
        return RedirectResponse(
            f"/login?next={urllib.parse.quote(destino, safe='/?=&')}",
            status_code=303,
        )
    return JSONResponse(status_code=401, content={"detail": "Sessão expirada ou ausente. Faça login."})


@app.middleware("http")
async def exigir_sessao_do_tenant(request: Request, call_next):
    """
    Exige sessao DE TENANT nos caminhos de tenant_auth.CAMINHOS_PROTEGIDOS.

    Independente do middleware do painel ADMIN, que segue exatamente como
    estava: cookies diferentes, listas diferentes, dominios de assinatura
    diferentes. Uma sessao de admin NAO abre o painel do cliente, e vice-versa.
    """
    from app.core import tenant_auth as _tenant

    if not _tenant.caminho_protegido(request.url.path):
        return await call_next(request)
    if _tenant.sessao_do_tenant(request) is not None:
        return await call_next(request)

    aceita = (request.headers.get("accept") or "").lower()
    if "text/html" in aceita:
        return RedirectResponse("/tenant/login", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "Sessão de cliente ausente ou expirada."})


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
DASHBOARDS_DIR = FRONTEND_DIR / "dashboards"
TENANTS_FILE = BASE_DIR / "tenants.json"

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

def _store_json_aposentado(*_args, **_kwargs):
    """
    O arquivo tenants.json foi APOSENTADO em 2026-09-01.

    Os dados vivem em `tenants` (Postgres) desde a migração; os 5 slugs que só
    existiam no arquivo foram para o banco antes da troca das rotas.

    Estas funções LEVANTAM em vez de devolver dado vazio, de propósito. Um
    `get_tenants_store()` que devolvesse `{}` faria uma rota esquecida
    responder "nenhum inquilino" — indistinguível de um banco vazio, e
    silencioso. Levantar transforma um chamador esquecido em erro visível na
    primeira execução, que é o que se quer descobrir.

    Verificado por grep em todo o app (Python, frontend, shell, compose) que
    NENHUM chamador restou — só as definições e comentários. Se isto levantar,
    é porque apareceu um caminho novo que precisa ir para o Postgres.
    """
    raise RuntimeError(
        "tenants.json foi aposentado em 2026-09-01. "
        "Os inquilinos vivem na tabela `tenants` do Postgres. "
        "Use as rotas /v1/tenants/* ou consulte o modelo Tenant diretamente."
    )


get_tenants_store = _store_json_aposentado
save_tenants_store = _store_json_aposentado

RESERVED_ROUTES = {
    "saas", "admin", "login", "cadastro", "register", "cliente", "clientes",
    "health", "v1", "dashboards", "static", "docs", "redoc", "openapi.json", "favicon.ico"
}

def call_evolution_api(endpoint: str, method: str = "GET", data: dict = None):
    url = f"{EVOLUTION_BASE_URL}{endpoint}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "ATENDIT-Core/1.0"
    }
    body_bytes = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            resp_body = res.read().decode("utf-8")
            return json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return json.loads(err_body)
        except Exception:
            return {"error": True, "status_code": e.code, "message": err_body}
    except Exception as e:
        return {"error": True, "message": str(e)}

async def ensure_evolution_instance(slug: str, modo_qrcode: bool = True):
    instance_name = f"atendit_{slug.replace('-', '_')}"
    payload = {
        "instanceName": instance_name,
        "token": slug,
        "qrcode": modo_qrcode,
        "integration": "WHATSAPP-BAILEYS"
    }
    call_evolution_api("/instance/create", method="POST", data=payload)

    # Blindagem Definitiva do Webhook (Auto-regeneração contra o ON DELETE CASCADE)
    try:
        from sqlalchemy import select as _sel
        from app.core.database import AsyncSessionLocal as _Sessao
        from app.models.tenant import Tenant as _Tenant
        import logging as _logging

        _log = _logging.getLogger("atendit.evolution")
        async with _Sessao() as sessao:
            res = await sessao.execute(_sel(_Tenant.id).where(_Tenant.slug == slug))
            tenant_id = res.scalar_one_or_none()

        if tenant_id:
            webhook_payload = {
                "webhook": {
                    "enabled": True,
                    "url": f"http://atendit-api:8000/webhook/evolution/{tenant_id}",
                    "webhookByEvents": False,
                    "webhookBase64": False,
                    "events": ["MESSAGES_UPSERT"],
                    "headers": {
                        "X-Webhook-Secret": settings.WEBHOOK_SECRET,
                        "Content-Type": "application/json"
                    }
                }
            }
            call_evolution_api(f"/webhook/set/{instance_name}", method="POST", data=webhook_payload)
            _log.info(f"[WEBHOOK AUTO-HEAL] Webhook garantido com HMAC para '{instance_name}' (Tenant ID: {tenant_id})")
        else:
            _log.warning(f"[WEBHOOK AUTO-HEAL] Slug '{slug}' nao encontrado no banco para vincular webhook.")
    except Exception as e:
        import logging as _logging
        _logging.getLogger("atendit.evolution").error(f"[WEBHOOK AUTO-HEAL ERROR] Falha ao auto-registrar webhook: {e}")

    return instance_name

# ---------------------------------------------------------------------------
# Routers modulares (app/routes/). Registrados AQUI, antes das rotas inline,
# porque o catch-all GET /{tenant_slug} no fim do arquivo capturaria qualquer
# caminho de um segmento que fosse declarado depois dele.
# ---------------------------------------------------------------------------
from app.core.security import exigir_token_interno
from app.routes import webhook as _webhook_routes
from app.routes import tenants as _tenants_routes
from app.routes import ai_config as _ai_config_routes
from app.routes import rag
from app.routes import meta as _rag_routes
from app.routes import hardware as _hardware_routes
from app.services.calendar import routes as _calendar_routes
from app.routes import logistics as _logistics_routes
from app.routes import intel_operacional as _intel_operacional_routes

from app.routes import conta_tenant as _conta_tenant
# Rotas de conta do cliente. ANTES do catch-all /{tenant_slug}, senao
# /tenant/login seria capturado como se fosse slug de inquilino.
app.include_router(_conta_tenant.router)
app.include_router(_webhook_routes.router)
app.include_router(_tenants_routes.router)
app.include_router(_ai_config_routes.router)
app.include_router(_rag_routes.router)
app.include_router(_hardware_routes.router)
app.include_router(_calendar_routes.router)
app.include_router(_logistics_routes.router)
app.include_router(_intel_operacional_routes.router)
from app.routes import video as _video_routes
app.include_router(_video_routes.router)
from app.routes import presenthia as _presenthia_routes
app.include_router(_presenthia_routes.router)



async def _semear_admin_inicial():
    """
    Cria o usuario 'admin' a partir de ADMIN_PASSWORD, uma unica vez.

    Idempotente e conservador: so age se a tabela estiver VAZIA. Se ja houver
    qualquer usuario, nao mexe -- senao uma variavel de ambiente esquecida no
    .env poderia ressuscitar uma senha antiga depois de o operador ter
    trocado a dele pelo painel.
    """
    import logging as _lg
    from sqlalchemy import func as _f, select as _s

    from app.core.database import AsyncSessionLocal as _Sessao
    from app.models.admin import AdminUser as _AdminUser

    _log = _lg.getLogger("atendit.seed")
    senha = (settings.ADMIN_PASSWORD or "").strip()
    try:
        async with _Sessao() as sessao:
            total = (await sessao.execute(_s(_f.count()).select_from(_AdminUser))).scalar_one()
            if total:
                _log.info(f"[SEED] admin_users ja tem {total} usuario(s); nada a semear.")
                return
            if not senha:
                _log.warning("[SEED] admin_users vazia e ADMIN_PASSWORD ausente: ninguem consegue logar.")
                return
            sessao.add(_AdminUser(username="admin", password_hash=_painel.gerar_hash(senha)))
            await sessao.commit()
        _log.info("[SEED] Usuario 'admin' criado a partir de ADMIN_PASSWORD.")
    except Exception as _e:
        # Os dois workers chamam o seed ao mesmo tempo; um insere e o outro
        # bate na UNIQUE de username. A restricao fez exatamente o trabalho
        # dela -- existe UM admin. Isso e ruido, nao falha.
        if "ix_admin_users_username" in str(_e) or "duplicate key" in str(_e):
            _log.info("[SEED] Outro worker semeou o admin primeiro; seguindo.")
        else:
            _log.error(f"[SEED] Falha ao semear o usuario admin: {_e}")


@app.on_event("startup")
async def _criar_tabelas_ausentes():
    """
    Cria as tabelas que faltam (checkfirst=True), de forma idempotente.

    ATENCAO: cria apenas tabela AUSENTE. Coluna nova em tabela existente NAO
    e criada aqui e nao emite erro - o codigo novo e que falha em runtime
    procurando coluna que nunca nasceu. Alteracao de tabela existente e
    ALTER TABLE manual + ajuste no model, ate existir migracao de verdade.
    """
    import logging as _lg
    from app.core.database import engine as _engine
    from app import models as _models
    _log = _lg.getLogger("atendit.schema")
    try:
        async with _engine.begin() as _conn:
            await _conn.run_sync(_models.Base.metadata.create_all, checkfirst=True)
        _log.info("[SCHEMA] create_all(checkfirst=True) concluido.")
    except Exception as _e:
        # Com --workers 2, os dois processos chamam create_all ao mesmo tempo e
        # um deles perde a corrida na criacao do TIPO do enum. O outro ja criou:
        # e ruido, nao falha. Qualquer outro erro continua sendo ERROR.
        if "pg_type_typname_nsp_index" in str(_e) or "already exists" in str(_e):
            _log.warning("[SCHEMA] Outro worker criou o schema primeiro; seguindo.")
        else:
            # Nao derruba a API: schema incompleto e melhor diagnosticado com o
            # servico no ar do que com ele em crash-loop.
            _log.error(f"[SCHEMA] Falha ao criar tabelas ausentes: {_e}")

    # SO agora: o seed precisa da tabela que acabou de ser criada. Registrar
    # como handler separado o fazia rodar ANTES do create_all -- os handlers
    # disparam na ordem de registro, nao na ordem de dependencia.
    await _semear_admin_inicial()


# ---------------------------------------------------------------------------
# NOTA (2026-08-31): estas rotas /v1/* NAO exigem X-Internal-Token.
# Nao e esquecimento. Elas sao chamadas pelo NAVEGADOR (index.html e
# cadastro.html) sem credencial nenhuma, porque o painel ainda nao tem
# sessao de usuario. Exigir o token aqui devolve 401 ao navegador e quebra
# os botoes de QR Code, Token, Copiloto, cadastro e salvar inquilino -- foi
# exatamente o que aconteceu e produziu "Erro ao gerar codigo: undefined".
#
# NAO reaplicar a protecao antes de existir login no painel. O caminho certo
# e sessao no navegador, nao token de servico embutido no JavaScript (que
# seria publico para qualquer visitante).
#
# As rotas maquina-a-maquina que o navegador NAO chama seguem protegidas:
#   POST /rag/upload/{tenant_id}, POST /tenants/, POST /ai-config/submit/{id}
# ---------------------------------------------------------------------------
async def _avisar_novo_lead(lead_id: str, nome: str, corpo: dict) -> None:
    """
    Avisa o responsavel por WhatsApp que chegou um lead novo.

    Roda fora do ciclo da requisicao. Engole qualquer excecao de proposito:
    neste ponto o lead JA esta gravado, e uma excecao aqui so poluiria o log
    de erros com algo que nao custou nenhum dado.
    """
    try:
        from app.services.evolution_service import evolution_service as _evo

        texto = (
            f"🔔 Novo lead na landing page: {nome}"
            f" — WhatsApp: {corpo.get('whatsapp') or '—'}"
            f" — Email: {corpo.get('email') or '—'}."
            f" Comentário: {corpo.get('comentario') or '—'}"
        )
        enviado = await _evo.send_text_message(
            instance_name=settings.ADMIN_NOTIFICATION_INSTANCE,
            recipient_number=settings.ADMIN_NOTIFICATION_PHONE,
            text=texto,
        )
        if enviado:
            logger.info(f"[LEAD] Aviso ao administrador entregue (lead {lead_id}).")
        else:
            logger.warning(
                f"[LEAD] Lead {lead_id} SALVO, mas o gateway recusou o aviso. "
                f"O contato esta na tabela leads e nao se perdeu."
            )
    except Exception as exc:
        logger.error(f"[LEAD] Lead {lead_id} SALVO, mas o aviso por WhatsApp falhou: {exc}")


@app.post("/v1/leads", include_in_schema=False)
async def receber_lead(request: Request, tarefas: BackgroundTasks):
    """
    Recebe o formulario de contato da landing page.

    PUBLICA de proposito: e um formulario aberto a visitantes. Nao entra em
    CAMINHOS_PROTEGIDOS -- exigir login aqui impediria justamente quem a
    pagina existe para captar.

    Grava de verdade. O formulario da referencia mostrava "mensagem
    registrada com sucesso" apenas escrevendo no console; confirmacao de
    gravacao sem gravacao e o que o PNL-01 existe para eliminar.
    """
    from app.core.database import AsyncSessionLocal as _Sessao
    from app.models.scheduling import Lead as _Lead

    try:
        corpo = await request.json()
    except Exception:
        corpo = {}

    nome = (corpo.get("nome") or "").strip()
    if not nome:
        return JSONResponse(status_code=400, content={"ok": False, "detail": "Informe seu nome."})

    try:
        async with _Sessao() as sessao:
            lead = _Lead(
                nome=nome[:150],
                whatsapp=(corpo.get("whatsapp") or "").strip()[:32] or None,
                email=(corpo.get("email") or "").strip()[:255] or None,
                comentario=(corpo.get("comentario") or "").strip() or None,
                origem="landing",
            )
            sessao.add(lead)
            await sessao.commit()
            await sessao.refresh(lead)
            lead_id = str(lead.id)
    except Exception as exc:
        logger.error(f"[LEAD] Falha ao gravar contato de '{nome}': {exc}")
        return JSONResponse(status_code=500, content={"ok": False, "detail": "Não consegui registrar agora. Tente novamente."})

    logger.info(f"[LEAD] Contato registrado: {lead_id} ({nome})")

    # Aviso ao responsavel, em SEGUNDO PLANO.
    #
    # Por que nao aqui, com await: o envio ao gateway leva de 1 a 19 segundos
    # (medido), e o visitante ficaria com o formulario girando esse tempo todo
    # por causa de um aviso que nao e problema dele. Em BackgroundTasks o
    # envio roda DEPOIS de a resposta ja ter ido embora -- e como o lead ja
    # foi commitado, nada do que acontecer aqui pode perde-lo.
    tarefas.add_task(_avisar_novo_lead, lead_id, nome, corpo)

    return {"ok": True, "lead_id": lead_id}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "atendit-saas-engine", "version": "1.0.0", "environment": "production"}

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_root():
    f = FRONTEND_DIR / "landing.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.is_file() else HTMLResponse("<h1>ATENDIT</h1>")

@app.get("/cadastro", response_class=HTMLResponse, include_in_schema=False)
@app.get("/register", response_class=HTMLResponse, include_in_schema=False)
def serve_cadastro():
    f = FRONTEND_DIR / "cadastro.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.is_file() else HTMLResponse("<h1>Cadastro</h1>")

@app.post("/login", include_in_schema=False)
async def painel_login(request: Request, response: Response):
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}
    usuario = (corpo.get("usuario") or corpo.get("username") or corpo.get("email") or "").strip()
    senha = corpo.get("senha") or corpo.get("password") or ""

    if not usuario or not senha:
        return JSONResponse(status_code=401, content={"detail": "Informe o usuário e a senha."})

    # 1. Tenta autenticar como Administrador da Plataforma (admin_users)
    conta_admin = await _painel.autenticar(usuario, senha)
    if conta_admin is not None:
        resposta = JSONResponse(content={"ok": True, "destino": "/admin"})
        resposta.set_cookie(
            key=_painel.NOME_COOKIE,
            value=_painel.emitir_cookie(),
            max_age=_painel.DURACAO_SESSAO_SEGUNDOS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        logger.info(f"[PANEL AUTH] Login administrativo efetuado por '{usuario}'.")
        return resposta

    # 2. Tenta autenticar como Inquilino / Cliente (tenant_users)
    from app.core import tenant_auth
    conta_tenant, motivo = await tenant_auth.autenticar(usuario, senha)
    if conta_tenant is not None:
        resposta = JSONResponse(content={"ok": True, "destino": "/tenant/painel"})
        resposta.set_cookie(
            key=tenant_auth.NOME_COOKIE,
            value=tenant_auth.emitir_cookie(conta_tenant.tenant_id, conta_tenant.id),
            max_age=tenant_auth.DURACAO_SESSAO_SEGUNDOS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        logger.info(f"[TENANT AUTH] Login de inquilino efetuado com sucesso por '{usuario}'.")
        return resposta

    # 3. Tratamento de mensagens específicas caso seja conta de tenant pendente
    if motivo == "nao_verificado":
        return JSONResponse(
            status_code=403,
            content={"detail": "Confirme seu e-mail antes de entrar. Procure o link que enviamos no seu cadastro."}
        )
    if motivo == "inativo":
        return JSONResponse(
            status_code=403,
            content={"detail": "Esta conta está desativada. Fale com o suporte."}
        )

    # Mensagem padronizada de segurança
    return JSONResponse(
        status_code=401, content={"detail": "Usuário ou senha incorretos."}
    )


@app.get("/logout", include_in_schema=False)
@app.post("/logout", include_in_schema=False)
async def painel_logout():
    resposta = RedirectResponse("/login", status_code=303)
    resposta.delete_cookie(_painel.NOME_COOKIE, path="/")
    logger.info("[PANEL AUTH] Logout efetuado.")
    return resposta


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def serve_login():
    f = FRONTEND_DIR / "login.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.is_file() else HTMLResponse("<h1>Login</h1>")

@app.get("/SaaS", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def serve_admin():
    f = FRONTEND_DIR / "admin.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.is_file() else HTMLResponse("<h1>Admin SaaS</h1>")

# /cliente e /clientes foram REMOVIDAS em 2026-09-02.
#
# Elas nao eram telas: redirecionavam para "/mani", com o slug cravado no
# codigo. Isso amarrava duas URLs publicas a UM inquilino especifico -- e o
# "mani" (Clube Pomerode) era resíduo da migração do tenants.json, sem conta,
# agenda, documento ou compromisso. Ele foi apagado junto.
#
# Os dois caminhos seguem em RESERVED_ROUTES, entao o catch-all /{tenant_slug}
# devolve 404 para eles em vez de tentar servir um inquilino chamado "cliente".

@app.get("/LOGO ATENDIT STICH.jpg", include_in_schema=False)
@app.get("/LOGO%20ATENDIT%20STICH.jpg", include_in_schema=False)
def serve_logo():
    for name in ["LOGO ATENDIT STICH.jpg", "image_772e18.jpg"]:
        logo_path = FRONTEND_DIR / name
        if logo_path.is_file():
            return FileResponse(str(logo_path), media_type="image/jpeg")
    return JSONResponse(status_code=404, content={"error": "Logo not found"})

@app.post("/v1/auth/register")
async def auth_register(request: Request, tarefas: BackgroundTasks):
    """
    Cadastro REAL de cliente: grava em `tenants` + `tenant_users`, no Postgres.

    Substituiu a versao que escrevia no arquivo tenants.json, DESCARTAVA a
    senha recebida em silencio e devolvia "token": "atendit-token-<slug>-2026"
    -- um literal previsivel que parecia credencial e nao era.

    O token sumiu da resposta de proposito: autenticacao agora vem do
    POST /tenant/login, contra o hash bcrypt de tenant_users.

    Desde 2026-09-01 as quatro rotas do painel admin (/v1/tenants/,
    /v1/tenants/{slug}, /v1/tenants/update e /v1/agent/config) tambem leem e
    escrevem no Postgres, entao o inquilino criado aqui APARECE no painel --
    o que nao acontecia enquanto o painel lia o tenants.json.
    """
    try:
        corpo = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "JSON inválido"})

    from app.routes.conta_tenant import registrar_conta
    return await registrar_conta(corpo, tarefas)


# ---------------------------------------------------------------------------
# Painel admin: tenants e configuracao do agente.
#
# Migradas do arquivo tenants.json para o Postgres em 2026-09-01. O JSON criava
# uma DIVERGENCIA silenciosa: o cadastro publico gravava no banco e o painel
# lia o arquivo, entao inquilino novo simplesmente nao aparecia para o admin.
#
# O FORMATO das respostas foi preservado byte a byte no que o frontend le
# (admin.html itera Object.keys() do dicionario; index.html le .tenant.name e
# .agent_config). Mudar o armazenamento sem mudar o contrato mantem as telas
# funcionando sem edicao.
#
# Onde cada campo legado passou a morar, sem inventar coluna nova:
#   name       -> tenants.name
#   email      -> tenants.admin_email
#   status     -> derivado de tenants.is_active
#   phone      -> tenants.meta_data['phone']
#   ai_number  -> tenants.meta_data['ai_number']
#   agent_*    -> ai_configs.agent_name + ai_configs.meta_data{tone,questions}
#
# phone/ai_number NAO foram para tenants.whatsapp_number_e164 de proposito:
# aquela coluna e escrita pelo fluxo de pareamento, e gravar valor de
# formulario ali corromperia o estado real da instancia da Evolution.
# ---------------------------------------------------------------------------
def _tenant_para_dicionario(inquilino, cfg=None) -> dict:
    md = inquilino.meta_data or {}
    agente = {}
    if cfg is not None:
        cmd = cfg.meta_data or {}
        agente = {
            "name": cfg.agent_name,
            "tone": cmd.get("tone", "Objetivo e Claro"),
            "questions": cmd.get("questions", []),
            "departments": cmd.get("departments", []),
        }
    return {
        "slug": inquilino.slug,
        "name": inquilino.name,
        "email": inquilino.admin_email,
        "phone": md.get("phone", ""),
        "ai_number": md.get("ai_number", ""),
        "status": "Operacional" if inquilino.is_active else "Inativo",
        "agent_config": agente,
    }


async def _carregar_tenants(sessao, slug=None):
    """Devolve [(Tenant, AIConfig|None)] em uma unica ida ao banco."""
    from sqlalchemy import select as _sel

    from app.models.tenant import AIConfig as _Cfg, Tenant as _T

    consulta = _sel(_T, _Cfg).outerjoin(_Cfg, _Cfg.tenant_id == _T.id)
    if slug is not None:
        consulta = consulta.where(_T.slug == slug)
    return (await sessao.execute(consulta.order_by(_T.created_at))).all()


@app.get("/v1/tenants/")
async def list_tenants(request: Request):
    """
    Dicionario indexado por slug -- formato que admin.html consome.

    Admin ve todos. Sessao de TENANT ve APENAS o proprio inquilino: devolver a
    lista inteira aqui entregaria nome e e-mail de todos os clientes a
    qualquer um deles. Filtrar (em vez de 403) mantem a rota util para o
    painel do cliente sem vazar o resto.
    """
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal as _S

    papel = await _autz.exigir_acesso_ao_tenant(request)
    meu_slug = None if papel == "admin" else await _autz.slug_da_sessao(request)

    async with _S() as sessao:
        linhas = await _carregar_tenants(sessao)
        return {
            t.slug: _tenant_para_dicionario(t, c)
            for t, c in linhas
            if meu_slug is None or t.slug == meu_slug
        }


@app.get("/v1/tenants/{tenant_slug}")
async def get_tenant(tenant_slug: str, request: Request):
    """
    Busca um inquilino. Slug inexistente devolve um objeto SINTETICO com 200,
    nao 404.

    Comportamento PRESERVADO de proposito. Verificado em 2026-09-01 que
    index.html ja trata resposta nao-ok montando exatamente este mesmo objeto
    no `else`/`catch` -- entao 404 tambem funcionaria. Como as duas opcoes sao
    equivalentes para o unico consumidor, mantive a atual: trocar seria mudar
    contrato publico sem ganho.
    """
    from app.core.database import AsyncSessionLocal as _S

    from app.core import autorizacao as _autz

    slug = (tenant_slug or "").strip().lower()
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    async with _S() as sessao:
        linhas = await _carregar_tenants(sessao, slug)
        if linhas:
            inquilino, cfg = linhas[0]
            return _tenant_para_dicionario(inquilino, cfg)

    return {
        "slug": slug,
        "name": slug.capitalize(),
        "email": "",
        "phone": "",
        "ai_number": "",
        "status": "Operacional",
        "agent_config": {
            "name": slug.capitalize(),
            "tone": "Objetivo e Claro",
            "questions": ["Qual seu nome?", "Qual o assunto do contato?", "Você já é associado?"],
        },
    }


@app.post("/v1/tenants/update")
async def update_tenant(request: Request):
    from sqlalchemy import select as _sel

    from app.core.database import AsyncSessionLocal as _S
    from app.models.tenant import Tenant as _T

    try:
        corpo = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "JSON inválido"})

    slug = (corpo.get("slug") or "").strip().lower()
    if not slug:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Slug é obrigatório"})

    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    async with _S() as sessao:
        inquilino = (
            await sessao.execute(_sel(_T).where(_T.slug == slug))
        ).scalar_one_or_none()

        if inquilino is None:
            # Antes, o store criava a chave na hora. Manter isso agora
            # significaria criar inquilino no banco por digitacao de slug --
            # bem mais caro de desfazer que uma chave num JSON.
            return JSONResponse(status_code=404, content={
                "status": "error",
                "message": f"Inquilino '{slug}' não existe. Cadastre-o antes de editar."})

        if "name" in corpo:
            inquilino.name = (corpo.get("name") or inquilino.name)[:255]
        if "email" in corpo:
            inquilino.admin_email = (corpo.get("email") or "")[:255]

        # meta_data e coluna JSON: alterar o dicionario NO LUGAR nao marca o
        # objeto como sujo, e o UPDATE simplesmente nao sai. Tem de ser um
        # dicionario NOVO atribuido ao atributo.
        md = dict(inquilino.meta_data or {})
        if "phone" in corpo:
            md["phone"] = (corpo.get("phone") or "").strip()
        if "ai_number" in corpo:
            md["ai_number"] = (corpo.get("ai_number") or "").strip()
        inquilino.meta_data = md

        await sessao.commit()
        linhas = await _carregar_tenants(sessao, slug)
        inq, cfg = linhas[0]
        dados = _tenant_para_dicionario(inq, cfg)

    logger.info(f"[TENANT UPDATE] '{slug}' atualizado no Postgres.")
    return {"status": "success", "tenant": dados}


@app.post("/v1/agent/config")
async def save_agent_config(request: Request):
    """Grava em ai_configs. Nenhuma coluna nova: agent_name + meta_data."""
    from sqlalchemy import select as _sel

    from app.core.database import AsyncSessionLocal as _S
    from app.models.tenant import AIConfig as _Cfg, Tenant as _T

    try:
        corpo = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "JSON inválido"})

    slug = (corpo.get("slug") or "").strip().lower()
    if not slug:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Slug é obrigatório"})

    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    perguntas = corpo.get("questions", [])
    if isinstance(perguntas, str):
        try:
            perguntas = json.loads(perguntas)
        except Exception:
            perguntas = [perguntas]

    async with _S() as sessao:
        inquilino = (
            await sessao.execute(_sel(_T).where(_T.slug == slug))
        ).scalar_one_or_none()
        if inquilino is None:
            return JSONResponse(status_code=404, content={
                "status": "error", "message": f"Inquilino '{slug}' não existe."})

        cfg = (
            await sessao.execute(_sel(_Cfg).where(_Cfg.tenant_id == inquilino.id))
        ).scalar_one_or_none()
        if cfg is None:
            cfg = _Cfg(tenant_id=inquilino.id)
            sessao.add(cfg)

        cfg.agent_name = (corpo.get("agent_name") or inquilino.name or "Atendente Virtual")[:100]
        # Mesmo cuidado do meta_data acima: dicionario NOVO, nunca mutacao.
        cmd = dict(cfg.meta_data or {})
        cmd["tone"] = corpo.get("tone", cmd.get("tone", "Objetivo e Claro"))
        cmd["questions"] = perguntas
        if "departments" in corpo:
            cmd["departments"] = corpo.get("departments") if isinstance(corpo.get("departments"), list) else []
        cfg.meta_data = cmd

        await sessao.commit()
        linhas = await _carregar_tenants(sessao, slug)
        inq, c = linhas[0]
        dados = _tenant_para_dicionario(inq, c)

    logger.info(f"[AGENT CONFIG] Parâmetros de IA de '{slug}' gravados em ai_configs.")
    return {"status": "success", "message": "Parâmetros da IA gravados!", "tenant": dados}



# ---------------------------------------------------------------------------
# ROTAS DE CONEXÃO E STATUS WHATSAPP (Evolution API)
# ---------------------------------------------------------------------------

@app.get("/v1/whatsapp/status")
async def get_wa_status(request: Request):
    slug = (request.query_params.get("slug") or "default").strip().lower()
    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    instance_name = f"atendit_{slug.replace('-', '_')}"
    res = call_evolution_api(f"/instance/connectionState/{instance_name}", method="GET")
    state = "close"
    if isinstance(res, dict) and "instance" in res:
        state = res["instance"].get("state", "close")
    return {"status": "success", "instance_name": instance_name, "state": state}


@app.post("/v1/whatsapp/reset")
async def api_wa_reset(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}
    slug = (corpo.get("slug") or "").strip().lower()
    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    instance_name = f"atendit_{slug.replace('-', '_')}"
    call_evolution_api(f"/instance/logout/{instance_name}", method="DELETE")
    return {"status": "success", "message": "Sessão encerrada com sucesso!"}


@app.post("/v1/whatsapp/qrcode")
async def generate_wa_qrcode(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    slug = body.get("slug", "default").strip().lower()

    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    instance_name = await ensure_evolution_instance(slug, modo_qrcode=False)

    connect_res = {}
    for attempt in range(4):
        time.sleep(1.5)
        connect_res = call_evolution_api(f"/instance/connect/{instance_name}", method="GET")
        if isinstance(connect_res, dict) and (connect_res.get("base64") or (isinstance(connect_res.get("qrcode"), dict) and connect_res["qrcode"].get("base64"))):
            break

    base64_qr = None
    if isinstance(connect_res, dict):
        base64_qr = connect_res.get("base64")
        if not base64_qr and isinstance(connect_res.get("qrcode"), dict):
            base64_qr = connect_res["qrcode"].get("base64")

    if not base64_qr:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "A Evolution API está iniciando a sessão. Clique novamente em 'Gerar Código QR' em 3 segundos."
        })

    return {"status": "success", "slug": slug, "instance_name": instance_name, "qrcode_url": base64_qr}

from pydantic import BaseModel

class PairCodeRequest(BaseModel):
    instance_name: str
    phone: str

@app.post("/v1/whatsapp/pair-code")
async def api_pair_code(corpo: dict, request: Request):
    slug = (corpo.get("slug") or "").strip().lower()
    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    phone = corpo.get("phone") or corpo.get("number") or ""
    if not phone:
        return {"success": False, "error": "Informe o numero de telefone."}

    instance_name = f"atendit_{slug.replace('-', '_')}"

    # 1. Garante que a instancia exista com qrcode=False estrito (elimina rotacao de chaves da Meta)
    call_evolution_api(f"/instance/delete/{instance_name}", method="DELETE")
    import time; time.sleep(0.8)
    call_evolution_api("/instance/create", method="POST", data={
        "instanceName": instance_name,
        "token": slug,
        "qrcode": False,
        "integration": "WHATSAPP-BAILEYS"
    })
    time.sleep(1.2)

    # 2. Requisita o codigo por telefone com exclusividade
    result = await generate_wa_pair_code(instance_name, phone)
    ok = bool(result.get("success"))
    result["status"] = "success" if ok else "error"
    if not ok:
        result["message"] = result.get("message") or result.get("error") or "Nao foi possivel gerar o codigo."
    return result
@app.get("/dashboards/{view_name}", include_in_schema=False)
async def get_dashboard_view(view_name: str, request: Request):
    """
    Serve o HTML de uma tela do painel.

    Aceita sessao de admin OU de tenant, SEM conferencia de inquilino -- e
    correto aqui, e a razao precisa importar: estes arquivos sao CASCAS
    estaticas. Nenhum dado de inquilino esta dentro deles; tudo e buscado
    depois, pelo JavaScript, nas rotas /v1/*, que CONFEREM o inquilino.
    Bloquear a casca nao protegeria nada e so quebraria o painel.

    ⚠️ Se algum dia uma view passar a ser renderizada COM dados no servidor,
    esta rota precisa de exigir_acesso_ao_tenant(request, slug=...).
    """
    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request)

    safe_name = os.path.basename(view_name.strip()).replace(".html", "")
    target_file = (DASHBOARDS_DIR / f"{safe_name}.html").resolve()
    if not str(target_file).startswith(str(DASHBOARDS_DIR.resolve())) or not target_file.is_file():
        return JSONResponse(status_code=404, content={"error": f"Dashboard '{view_name}' not found"})
    with open(target_file, "r", encoding="utf-8") as f:
        return JSONResponse(content=f.read())

@app.post("/v1/ai/copilot")
async def copilot_chat(request: Request):
    from app.core import autorizacao as _autz
    await _autz.exigir_acesso_ao_tenant(request)
    return {"status": "success", "response": "Comando processado com sucesso pelo Copiloto ATENDIT."}

@app.get("/{tenant_slug}", response_class=HTMLResponse, include_in_schema=False)
def serve_tenant_instance(tenant_slug: str, request: Request):
    """
    Painel de um inquilino especifico, usado pelo ADMIN para gerenciar aquele
    cliente. NAO confundir com /tenant/painel, que e o autoatendimento do
    proprio cliente e usa a sessao dele.

    🔴 Exige sessao de admin desde 2026-09-02. Antes servia o HTML para
    qualquer visitante, com o slug vindo da URL -- bastava digitar /manitest.
    As chamadas de API que a tela faz ja exigiam sessao, entao o que vazava era
    a casca; mesmo assim, a casca revela a existencia e o nome do inquilino.
    """
    # Rota reservada e 404 ANTES de qualquer checagem de sessao: um caminho que
    # nao existe deve dizer que nao existe, e nao mandar a pessoa fazer login
    # para depois descobrir que nao havia nada ali.
    slug_clean = tenant_slug.strip().lower()
    if slug_clean in RESERVED_ROUTES:
        raise HTTPException(status_code=404, detail="Not found")

    # A checagem mora AQUI, e nao em CAMINHOS_SOMENTE_ADMIN, porque este e o
    # catch-all: um prefixo naquela lista casaria caminhos publicos.
    if not _painel.sessao_ativa(request):
        return _recusar_sem_sessao(request, "/login")
    f = FRONTEND_DIR / "index.html"
    return HTMLResponse(f.read_text(encoding="utf-8")) if f.is_file() else HTMLResponse(f"<h1>Instância: {slug_clean}</h1>")



async def generate_wa_pair_code(instance_name: str, phone: str):
    import httpx
    import re
    import asyncio

    # 1. Checagem preventiva: se ja estiver conectado, orienta o usuario
    try:
        checa_estado = call_evolution_api(f"/instance/connectionState/{instance_name}", method="GET")
        if isinstance(checa_estado, dict) and checa_estado.get("instance", {}).get("state") == "open":
            return {
                "success": False,
                "message": "O WhatsApp já está conectado nesta instância! Para parear um novo número, clique antes em 'Limpar Sessão'."
            }
    except Exception as e:
        logger.warning(f"[PAIR_CODE] Falha ao checar estado previo: {e}")

    sanitized_phone = re.sub("[^0-9]", "", phone)
    endpoint = f"/instance/connect/{instance_name}"
    url = f"{EVOLUTION_BASE_URL}{endpoint}?number={sanitized_phone}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "ATENDIT-Core/1.0"
    }

    # Polling estendido: 7 tentativas com 1.5s (~11s de janela para retorno da Meta)
    max_retries = 7
    timeout_limit = 12.0

    async with httpx.AsyncClient(timeout=timeout_limit) as client:
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.get(url, headers=headers)
                logger.info(f"[PAIR_CODE] Tentativa {attempt}/{max_retries} para {sanitized_phone} -> HTTP {response.status_code}")

                if response.status_code == 200:
                    data = response.json() if response.content else {}
                    pairing_code = (
                        data.get("pairingCode")
                        or data.get("code")
                        or data.get("pairing_code")
                        or (data.get("qrcode") or {}).get("pairingCode")
                    )

                    clean_code = str(pairing_code or "").replace("-", "").strip()
                    if clean_code and len(clean_code) == 8:
                        formatted_code = f"{clean_code[:4]}-{clean_code[4:]}"
                        logger.info(f"[PAIR_CODE] Token gerado com sucesso na tentativa {attempt}: {formatted_code}")
                        return {"success": True, "pairing_code": formatted_code, "raw": data}

            except Exception as e:
                logger.error(f"[PAIR_CODE] Excecao na tentativa {attempt}: {e}")

            await asyncio.sleep(1.5)

    return {
        "success": False,
        "message": "Tempo limite esgotado aguardando resposta da Meta. Verifique se o número possui WhatsApp ativo ou tente novamente em instantes."
    }

from app.routes import rag
from app.routes import meta as rag_module
app.include_router(rag_module.router, prefix="/v1/rag")


# ---------------------------------------------------------------------------
# ROTAS DE RESGATE, ANÁLISE E GESTÃO DE CONVERSAS (WHATSAPP REAL)
# ---------------------------------------------------------------------------

@app.get("/v1/whatsapp/chats")
async def listar_chats_whatsapp(request: Request):
    """Resgata as conversas ativas do inquilino com contadores e última mensagem."""
    from sqlalchemy import text as _sql_text
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal as _S

    slug = (request.query_params.get("tenant_slug") or request.query_params.get("slug") or "").strip().lower()
    if not slug:
        slug = await _autz.slug_da_sessao(request)
    if slug:
        await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    async with _S() as sessao:
        t_res = await sessao.execute(
            _sql_text("SELECT id, evolution_instance, slug, name FROM tenants WHERE slug = :slug"),
            {"slug": slug}
        )
        t_row = t_res.first()
        if not t_row:
            return []

        inst_nome = t_row[1] or f"atendit_{slug.replace('-', '_')}"
        i_res = await sessao.execute(
            _sql_text('SELECT id FROM "Instance" WHERE name = :name LIMIT 1'),
            {"name": inst_nome}
        )
        i_row = i_res.first()
        if not i_row:
            return []
        inst_id = i_row[0]

        chats_res = await sessao.execute(_sql_text('''
            SELECT c.id, c."remoteJid", c.name, c.labels, c."updatedAt", c."unreadMessages",
                   (SELECT m.message->>'conversation'
                    FROM "Message" m
                    WHERE m."instanceId" = c."instanceId"
                      AND m.key->>'remoteJid' = c."remoteJid"
                    ORDER BY m."messageTimestamp" DESC
                    LIMIT 1) AS last_msg,
                   (SELECT m."messageTimestamp"
                    FROM "Message" m
                    WHERE m."instanceId" = c."instanceId"
                      AND m.key->>'remoteJid' = c."remoteJid"
                    ORDER BY m."messageTimestamp" DESC
                    LIMIT 1) AS last_ts
            FROM "Chat" c
            WHERE c."instanceId" = :inst_id
            ORDER BY c."updatedAt" DESC
            LIMIT 100
        '''), {"inst_id": inst_id})

        lista = []
        for r in chats_res.fetchall():
            labels = r[3] or {}
            st = labels.get("status", "ia")
            contato_num = (r[1] or "").split("@")[0].split(":")[0]
            lista.append({
                "id": r[0],
                "remoteJid": r[1],
                "contact": contato_num,
                "client_name": r[2] or contato_num,
                "last_message": r[6] or "Nenhuma mensagem registrada",
                "last_timestamp": r[7],
                "status": st,
                "unread": r[5] or 0,
                "updated_at": r[4].strftime("%d/%m/%Y %H:%M") if r[4] else ""
            })
        return lista


@app.get("/v1/whatsapp/chats/{target}/messages")
async def obter_transcricao_chat(target: str, request: Request):
    """Resgata a transcrição completa de mensagens de uma conversa para auditoria e análise."""
    from datetime import datetime as _dt
    from sqlalchemy import text as _sql_text
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal as _S

    slug = (request.query_params.get("tenant_slug") or request.query_params.get("slug") or "").strip().lower()
    if not slug:
        slug = await _autz.slug_da_sessao(request)
    if slug:
        await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    async with _S() as sessao:
        t_res = await sessao.execute(
            _sql_text("SELECT id, evolution_instance, slug FROM tenants WHERE slug = :slug"),
            {"slug": slug}
        )
        t_row = t_res.first()
        if not t_row:
            return []
        inst_nome = t_row[1] or f"atendit_{slug.replace('-', '_')}"
        i_res = await sessao.execute(
            _sql_text('SELECT id FROM "Instance" WHERE name = :name LIMIT 1'),
            {"name": inst_nome}
        )
        i_row = i_res.first()
        if not i_row:
            return []
        inst_id = i_row[0]

        remote_jid = target
        if "@" not in target:
            c_res = await sessao.execute(
                _sql_text('SELECT "remoteJid" FROM "Chat" WHERE id = :cid AND "instanceId" = :iid LIMIT 1'),
                {"cid": target, "iid": inst_id}
            )
            c_row = c_res.first()
            if c_row:
                remote_jid = c_row[0]
            else:
                remote_jid = f"{target}@s.whatsapp.net"

        msgs_res = await sessao.execute(_sql_text('''
            SELECT id, key, "pushName", message, "messageTimestamp", source, status
            FROM "Message"
            WHERE "instanceId" = :inst_id
              AND key->>'remoteJid' = :remote_jid
            ORDER BY "messageTimestamp" ASC
            LIMIT 300
        '''), {"inst_id": inst_id, "remote_jid": remote_jid})

        resultado = []
        for m in msgs_res.fetchall():
            m_key = m[1] or {}
            m_msg = m[3] or {}
            from_me = bool(m_key.get("fromMe", False))

            texto = ""
            if "conversation" in m_msg:
                texto = m_msg["conversation"]
            elif "extendedTextMessage" in m_msg:
                texto = m_msg["extendedTextMessage"].get("text", "")
            elif "imageMessage" in m_msg:
                texto = f"[Imagem] {m_msg['imageMessage'].get('caption', '')}"
            else:
                texto = str(m_msg) if m_msg else ""

            hora = _dt.fromtimestamp(m[4]).strftime("%d/%m %H:%M") if m[4] else ""
            resultado.append({
                "id": m[0],
                "from_me": from_me,
                "sender": m[2] or ("Atendente / IA" if from_me else "Cliente"),
                "text": texto,
                "time": hora,
                "status": m[6] or "DELIVERED"
            })
        return resultado


@app.post("/v1/whatsapp/chats/{target}/status")
async def alterar_status_chat(target: str, request: Request):
    """Altera o estado da conversa: transbordo humano, retomada de IA ou encerramento."""
    from sqlalchemy import text as _sql_text
    from app.core import autorizacao as _autz
    from app.core.database import AsyncSessionLocal as _S

    corpo = await request.json()
    slug = (corpo.get("slug") or request.query_params.get("tenant_slug") or "").strip().lower()
    if not slug:
        slug = await _autz.slug_da_sessao(request)
    if slug:
        await _autz.exigir_acesso_ao_tenant(request, slug=slug)

    novo_status = (corpo.get("status") or "ia").strip().lower()

    async with _S() as sessao:
        t_res = await sessao.execute(
            _sql_text("SELECT id, evolution_instance, slug FROM tenants WHERE slug = :slug"),
            {"slug": slug}
        )
        t_row = t_res.first()
        if not t_row:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Tenant não encontrado"})

        inst_nome = t_row[1] or f"atendit_{slug.replace('-', '_')}"
        i_res = await sessao.execute(
            _sql_text('SELECT id FROM "Instance" WHERE name = :name LIMIT 1'),
            {"name": inst_nome}
        )
        i_row = i_res.first()
        if not i_row:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Instância não encontrada"})
        inst_id = i_row[0]

        await sessao.execute(_sql_text('''
            UPDATE "Chat"
            SET labels = jsonb_set(COALESCE(labels, '{}'::jsonb), '{status}', to_jsonb(:st::text)),
                "updatedAt" = NOW()
            WHERE "instanceId" = :inst_id AND (id = :target OR "remoteJid" = :target)
        '''), {"st": novo_status, "inst_id": inst_id, "target": target})
        await sessao.commit()

    return {"status": "success", "new_status": novo_status}

# ============================================================================
# CANARY ROUTE: Presenthia Feature Flag Validation (F0.11)
# ============================================================================
from app.core.feature_flags import flag_on

@app.get("/api/v1/presenthia/canary", dependencies=[Depends(flag_on("presenthia_core"))], include_in_schema=False)
async def presenthia_canary_status():
    return {"status": "success", "feature": "presenthia_core", "message": "Presenthia Flag Ativa"}

# ============================================================================
# PRESENTHIA STATIC ASSETS & CONSOLE (F0.6 / F0.9 / F0.10)
# ============================================================================
from fastapi.staticfiles import StaticFiles

_presenthia_assets_path = FRONTEND_DIR / "presenthia_assets"
if _presenthia_assets_path.is_dir():
    app.mount("/presenthia/assets", StaticFiles(directory=str(_presenthia_assets_path)), name="presenthia_static_assets")

@app.get("/presenthia/console", response_class=HTMLResponse, dependencies=[Depends(flag_on("presenthia_core"))], include_in_schema=False)
async def serve_presenthia_console_dashboard():
    target = FRONTEND_DIR / "presenthia_console.html"
    if target.is_file():
        return HTMLResponse(target.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Presenthia Console - Carregando</h1>", status_code=200)
