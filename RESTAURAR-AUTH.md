# RESTAURAR-AUTH.md — segredos de autenticação e cifra

> Documento operacional. **Nunca** registrar aqui o VALOR de nenhum segredo —
> apenas o nome, onde vive e o que acontece se for perdido.
> Última atualização: **2026-09-01**.

---

## Documentos irmãos

| Arquivo | Assunto |
|---|---|
| `RUNBOOK-WHATSAPP.md` | operação da instância Evolution. **Leia antes de recriar instância**: o webhook é apagado por `CASCADE` e precisa de recadastro manual |
| `backend/PENDENCIAS.md` | itens em aberto do roadmap |

---

## 🔴 CALENDAR_ENCRYPTION_KEY — perder esta chave invalida TODAS as conexões de calendário

**O que é:** chave **Fernet** (44 caracteres, base64 urlsafe) usada para cifrar a
coluna `calendar_connections.credentials_encrypted`, onde ficam os *refresh
tokens* de OAuth do Google Calendar, Microsoft 365, CalDAV e Calendly de cada
inquilino.

**Onde vive:**

| Local | Observação |
|---|---|
| `/home/ubuntu/atendit/.env` (600, `ubuntu:ubuntu`) | fonte da verdade |
| env dos containers `atendit-api`, `atendit-celery-worker`, `atendit-celery-beat` | injetada pelo `docker-compose.yml` |
| Gerenciador de senhas do usuário | ⚠️ **obrigatório** — é a única cópia fora da VPS |

### O que acontece se ela for perdida

**Os dados não são recuperáveis.** Fernet é cifra simétrica autenticada: sem a
chave exata, `credentials_encrypted` é ruído. Não existe recuperação parcial,
força bruta viável, nem "chave mestra".

Consequência concreta, por inquilino:

1. O backend não consegue mais renovar o *access token* do Google.
2. Toda consulta de disponibilidade e toda criação de evento no calendário
   externo passa a falhar.
3. **Cada inquilino precisa refazer o fluxo OAuth do zero** — o que exige ação
   do cliente final, não do operador. Num SaaS com muitos inquilinos, isso é um
   incidente de dias, não de minutos.

⚠️ **Modo de falha silencioso:** se a chave for TROCADA (não perdida), o backend
sobe normalmente e o painel abre. A falha só aparece quando alguém tenta
agendar — e aparece como "não consegui acessar sua agenda", que parece problema
do Google. **Trocar a chave sem migrar os dados cifrados é indistinguível de uma
falha do provedor.**

### Se precisar rotacionar

Rotação exige **decifrar com a chave antiga e recifrar com a nova, numa única
transação**, com as duas chaves disponíveis ao mesmo tempo. Não existe atalho.
Procedimento mínimo:

1. Backup do banco **antes** (`pg_dump` de `atendit_db`).
2. Guardar a chave antiga; gerar a nova (`Fernet.generate_key()`).
3. Script que, para cada linha de `calendar_connections`, decifra com a antiga
   e recifra com a nova.
4. Só então trocar `CALENDAR_ENCRYPTION_KEY` no `.env` e recriar os containers.
5. Validar com um inquilino real **antes** de descartar a chave antiga.

### Backup

Cópias do `.env` ficam em `/home/ubuntu/atendit-segredos/<timestamp>/env.ANTES`
(dir 700, arquivo 600). ⚠️ **Esses backups estão na própria VPS** — perder a VPS
perde o backup. A cópia no gerenciador de senhas é a única fora dela.

---

## INTERNAL_API_TOKEN — autenticação das rotas administrativas

**O que é:** token hex de 64 caracteres (`openssl rand -hex 32`), conferido
contra o header **`X-Internal-Token`** em toda rota de escrita.

**Onde vive:** mesmos três lugares da chave acima.

**Rotas protegidas por ele:**

| Rota | |
|---|---|
| `POST /rag/upload/{tenant_id}` | ingestão de documentos |
| `POST /ai-config/submit/{tenant_id}` | grava chave de API de IA |
| `POST /tenants/` | cria inquilino |
| `POST /v1/tenants/update` | edita inquilino |
| `POST /v1/auth/register` | cria conta |
| `POST /v1/agent/config` | configura agente |
| `POST /v1/whatsapp/qrcode`, `POST /v1/whatsapp/pair-code` | pareia número |
| `POST /v1/ai/copilot` | copiloto |

**Não protegido:** `POST /webhook/evolution/{tenant_id}`. Quem chama é o gateway
Evolution, que não tem como enviar este header.

> ### ✅ CORRIGIDO NO MESMO DIA (2026-09-01) — leia a história inteira
> Esta linha já afirmou que a rota *"usa o mecanismo próprio
> (`WEBHOOK_SECRET`)"*. **Era falso**: o nome só existia dentro de um
> comentário em `app/core/security.py`, sem implementação nenhuma. Medido:
> **HTTP 200** pelo caminho público, sem cabeçalho, inclusive com `tenant_id`
> inexistente.
>
> **Agora é verdade.** `WEBHOOK_SECRET` existe (64 hex, `.env` 600, injetado
> nos três serviços Python), a Evolution o devolve no header
> `X-Webhook-Secret` a cada chamada, e a rota compara com
> `hmac.compare_digest` **antes de ler o corpo**. Header ausente ou errado ⇒
> **401**; segredo não configurado no servidor ⇒ **503**, nunca liberado.
>
> Comportamento completo, comando de recadastro e **teste negativo de
> reverificação** em `RUNBOOK-WHATSAPP.md`, seção *"AUTENTICAÇÃO DO WEBHOOK"*.
>
> **A lição que fica, e que vale para este documento inteiro:** o erro se
> propagou porque o comentário no código afirmou o mecanismo, este documento
> copiou a afirmação, e ninguém verificou se o nome existia fora do
> comentário. **Afirmação de segurança sem `grep` que a sustente é rumor.**

**Falha fechado:** se `INTERNAL_API_TOKEN` não estiver configurado, as rotas
respondem **503**, não liberam acesso. O contrário — token vazio batendo com
header vazio — transformaria erro de configuração em porta aberta.

---

## CONTAS DE CLIENTE (`tenant_users`) — o segundo sistema de login

Criado em **2026-09-01**. **São dois sistemas de login independentes**, e
confundi-los é o erro mais provável de quem chegar depois:

| | painel ADMIN | painel do CLIENTE |
|---|---|---|
| Tabela | `admin_users` | `tenant_users` |
| Login | `POST /login` | `POST /tenant/login` |
| Cookie | `atendit_painel` | `atendit_tenant` |
| Módulo | `app/core/panel_auth.py` | `app/core/tenant_auth.py` |
| Alcance | **todos** os inquilinos | só o inquilino dele |
| Protege | `/dashboards`, `/v1/agent/config`, … | `/tenant/painel` |

**Os dois cookies não se substituem**, e isso foi testado nas duas direções:
cookie de tenant em `/dashboards` ⇒ **401**; cookie de admin em
`/tenant/painel` ⇒ **303** para o login do cliente.

### 🔴 DUAS SESSÕES, UMA REGRA DE ISOLAMENTO — não simplifique isto

Desde **2026-09-01**, `/dashboards/*` e as rotas de configuração aceitam **os
dois tipos de sessão**. O que impede um cliente de ver o outro é uma regra só,
implementada num lugar só:

```
app/core/autorizacao.py  ->  exigir_acesso_ao_tenant(request, slug=... | tenant_id=...)

    sessão de ADMIN   -> passa para QUALQUER inquilino   (como sempre foi)
    sessão de TENANT  -> passa SÓ se o alvo for o inquilino DA SESSÃO
    sem sessão        -> 401
    alvo de outro     -> 403
```

**Por que 401 e 403 são diferentes:** 401 diz "identifique-se", 403 diz
"identificado, mas não é seu". Colapsar os dois num código só destrói o
diagnóstico de quem opera.

#### O middleware NÃO faz a conferência de inquilino — e isso é de propósito

`exigir_sessao_do_painel` só verifica que **existe** sessão. Quem confere
**qual** inquilino é cada rota, porque só ela sabe de onde vem o alvo: corpo do
POST em `/v1/agent/config`, path em `/rag/export/{tenant_id}`, query em
`/calendar/status/{tenant}`. Um middleware não tem como saber isso sem
consumir o corpo da requisição.

> ⚠️ **Consequência que precisa estar na cabeça de quem mexer aqui:** rota
> protegida que manipula dado de inquilino e **não chama
> `exigir_acesso_ao_tenant` fica aberta a qualquer tenant logado**. A lista de
> quem chama está logo abaixo. Rota nova entra nela.

#### Quem confere hoje (verificado por teste, não por leitura)

| Rota | De onde vem o alvo |
|---|---|
| `GET /v1/tenants/` | — (admin vê todos; tenant vê **só o dele**, filtrado) |
| `GET /v1/tenants/{slug}` | path |
| `POST /v1/tenants/update` | corpo (`slug`) |
| `POST /v1/agent/config` | corpo (`slug`) |
| `POST /v1/whatsapp/qrcode` | corpo (`slug`) |
| `POST /v1/whatsapp/pair-code` | corpo (`slug`) |
| `GET /rag/export/{tenant_id}/{arquivo}` | path (UUID) |
| `POST /rag/upload/{tenant_id}` | path (UUID) |
| `GET /calendar/oauth/{provider}/start` | query (`tenant`) |
| `GET /calendar/status/{tenant}` | path |
| `POST /calendar/disconnect/{tenant}` | path |
| `GET /calendar/appointments/{tenant}` | path |

**Exceções conscientes, com o motivo:**

- **`GET /dashboards/{view}`** aceita as duas sessões **sem** conferir
  inquilino. Correto porque estes arquivos são **cascas estáticas**: nenhum
  dado de cliente está dentro deles, tudo é buscado depois pelo JavaScript nas
  rotas `/v1/*`, que conferem. ⚠️ Se algum dia uma view passar a ser
  renderizada **com dados no servidor**, ela precisa da conferência.
- **`GET /calendar/oauth/{provider}/callback`** e **o webhook do Calendly**
  seguem abertos. Quem os chama é o Google/Microsoft/Calendly, que não têm
  cookie nenhum; eles se protegem por `state` assinado e HMAC.
- **`POST /rag/upload`** aceita `X-Internal-Token` **ou** sessão. O
  `Depends(exigir_token_interno)` saiu da assinatura porque o painel do cliente
  envia documento pelo **navegador**, que não tem como mandar aquele header.

### 🔴 TRÊS BURACOS PRÉ-EXISTENTES FECHADOS NO MESMO DIA

Apareceram ao mapear as rotas, e nenhum deles exigia sequer estar logado.
Ficam registrados porque a lição vale mais que a correção: **"o painel inteiro
é anônimo mesmo" envelheceu mal** — virou padrão que sobreviveu à chegada do
login.

| Rota | Era | Prova medida antes da correção |
|---|---|---|
| `GET /v1/tenants/` | público | **200** sem cookie, nome e e-mail de **todos** os inquilinos |
| `GET /rag/export/{tenant_id}/{arquivo}` | público | **200** com 254 bytes do documento real de um cliente |
| `/calendar/status\|disconnect\|appointments` | público | **200** listando as agendas conectadas; `disconnect` derrubaria a agenda de qualquer cliente |

O cabeçalho de `app/services/calendar/routes.py` **afirmava** a ausência de
autenticação ("SEM AUTENTICAÇÃO, de propósito, no modo desenvolvimento"). Foi
corrigido junto — doc que descreve o estado antigo como se fosse o atual é a
mesma classe de erro do `WEBHOOK_SECRET` que não existia.

### O painel do cliente: o slug vem da sessão

`/tenant/painel` reaproveita as **mesmas** telas de `/dashboards/*` do painel
admin — nenhuma view foi recriada. O `slug` é lido do cookie e **injetado pelo
servidor** no HTML; não existe `?slug=` nem `/tenant/painel/{slug}`.

Isso é **defesa em profundidade, não a defesa principal**. Mesmo que alguém
forje `window.currentTenantSlug` no console do navegador, as rotas `/v1/*`
conferem o inquilino da sessão e devolvem **403** — medido. A camada do painel
existe para que ele nem tente algo inválido.

**Deliberadamente fora do menu do cliente:** `faturamento`, que exibe um
payload PIX fictício (PNL-01). Mostrar cobrança falsa a um cliente real é pior
que não mostrar cobrança nenhuma.

### Duas separações deliberadas, e o que aconteceria sem elas

1. **Nome de cookie diferente.** Mesmo nome, mesmo domínio ⇒ o navegador
   sobrescreve um com o outro: entrar como cliente derrubaria a sessão de
   admin.
2. **Domínio de assinatura diferente** — `tenant_auth` prefixa a mensagem
   assinada com `tenant:v1:`. Os dois usam o **mesmo** `PANEL_SESSION_SECRET`;
   sem o prefixo, as assinaturas teriam a mesma forma e um cookie de admin
   poderia ser reapresentado como cookie de tenant.

⚠️ Trocar `PANEL_SESSION_SECRET` **derruba os dois** de uma vez.

### O hash é o mesmo código, não uma cópia

`tenant_auth` importa `gerar_hash`/`conferir_hash` de `panel_auth`. Duas
implementações de bcrypt seriam dois lugares para divergir — e a divergência
apareceria como "senha certa não entra", que é caro de diagnosticar.

### Fluxo de cadastro (o que grava, e o que NÃO grava)

```
POST /v1/auth/register   -> tenants + tenant_users, UMA transação, Postgres
                            senha em bcrypt, email_verificado = false
                            token de verificação, e-mail em segundo plano
GET  /v1/auth/verificar  -> email_verificado = true, token APAGADO (uso único)
POST /tenant/login       -> recusa enquanto não verificado (403, mensagem própria)
POST /v1/auth/esqueci-senha -> token de 1h, resposta SEMPRE genérica
POST /v1/auth/reset-senha   -> grava senha, invalida token, confirma o e-mail
```

> ### 🔴 TENANT NOVO NASCE SEM WHATSAPP E SEM CALENDÁRIO
> O cadastro **não cria instância da Evolution nem conecta agenda**. Conectar é
> passo manual do próprio cliente, em `/tenant/painel`, depois do primeiro
> login.
>
> **É deliberado:** criar instância no cadastro geraria uma instância órfã por
> formulário abandonado — e cada instância nasce com a chave **global** da
> Evolution embutida como token dela (ver SEC-09), então cada órfã é mais uma
> cópia do segredo espalhada.

### Decisões de exposição de informação

- **Login**: e-mail inexistente e senha errada devolvem **a mesma mensagem** e
  gastam o mesmo tempo (bcrypt descartável). Testado: respostas byte a byte
  idênticas.
- **A checagem de verificação vem DEPOIS da senha.** Responder "confirme seu
  e-mail" a quem errou a senha diria a um estranho que aquele endereço tem
  conta aqui.
- **"Esqueci minha senha" responde igual exista ou não a conta**, e o envio vai
  para segundo plano — o tempo de resposta também não pode denunciar.
- **Cadastro é a exceção:** e-mail duplicado devolve **409 com mensagem
  própria**. Aqui a regra genérica prejudicaria o dono da conta, que precisa
  saber que já tem cadastro para não ficar preso.

### Reset de senha confirma o e-mail junto

Quem recupera a senha pelo link provou que controla o endereço. Sem isso, uma
conta nunca verificada trocaria a senha e **continuaria barrada no login**, sem
caminho de saída.

---

## Demais segredos (referência cruzada)

| Segredo | Onde | Perda |
|---|---|---|
| `GEMINI_API_KEY` | `.env` + `ai_configs.api_key` por inquilino | recriável no Google AI Studio |
| `EVOLUTION_API_KEY` | `.env` + env do gateway | rotacionável; ver SEC-06/SEC-09 |
| `WEBHOOK_SECRET` | `.env` + env dos 3 serviços Python **e** no campo `headers` do webhook da Evolution | gere outro, grave no `.env`, recrie os containers **e recadastre o webhook** — as duas pontas têm de mudar juntas, senão a rota passa a recusar o gateway com 401 |
| `POSTGRES_PASSWORD` | `.env` + env do `atendit-db` | perda = perda de acesso ao banco |
| `REDIS_PASSWORD` | `.env` + `argv` do container | ver SEC-18 |
| Chave SSH | máquina local | ver seção 10 do `CLAUDE.md` |

---

## 🔴 SENHA DO PAINEL É TEMPORÁRIA — trocar ANTES do primeiro cliente

**Estado em 2026-09-01:** a senha do usuário `admin` foi trocada, a pedido, por
uma **senha fraca e temporária, escolhida para facilitar o teste manual**. Ela
tem 10 caracteres, segue padrão previsível (palavra + dígitos + símbolo) e
**cairia em ataque de dicionário em minutos**.

*(O valor não está registrado aqui — este documento não guarda valor de segredo,
ver o aviso no topo. Ele está com o usuário.)*

**Isso é aceitável hoje e deixa de ser no instante em que houver cliente real**,
porque a mesma sessão que essa senha abre dá acesso a:

- a configuração de IA e a base de RAG de **todos** os inquilinos;
- o pareamento de número de WhatsApp (`/v1/whatsapp/qrcode`, `/pair-code`);
- a agenda conectada de cada cliente.

**Como trocar** — não existe tela de troca de senha no painel, que é hoje o
item aberto mais próximo:

```bash
docker exec -i atendit-api python - <<'FIM'
import asyncio, sys
sys.path.insert(0, "/workspace")
SENHA_NOVA = "<cole aqui, gerada no gerenciador>"   # via STDIN, nunca em argv

async def principal():
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.core.panel_auth import gerar_hash, conferir_hash
    from app.models.admin import AdminUser
    async with AsyncSessionLocal() as s:
        alvo = (await s.execute(
            select(AdminUser).where(AdminUser.username == "admin"))).scalar_one()
        alvo.password_hash = gerar_hash(SENHA_NOVA)
        await s.commit(); await s.refresh(alvo)
        print("confere:", conferir_hash(SENHA_NOVA, alvo.password_hash))

asyncio.run(principal())
FIM
```

⚠️ **A senha entra por STDIN (heredoc), nunca como argumento** — argumento de
linha de comando é visível em `ps aux` para qualquer usuário da máquina.

⚠️ **Trocar a senha NÃO derruba quem já está logado.** O cookie é *stateless*:
uma sessão emitida com a senha antiga continua válida até as 12h expirarem.
Para revogar de imediato, trocar `PANEL_SESSION_SECRET` e recriar os containers.

---

## ADMIN_PASSWORD e PANEL_SESSION_SECRET — sessão do painel

Adicionados em **2026-09-01**. Substituem o "login" anterior, que era falso:
o `login.html` apenas gravava `localStorage` e redirecionava, sem validar nada.

### 🔴 O mecanismo MUDOU em 2026-09-01: a senha saiu do `.env` e foi para o banco

A descrição abaixo valia até 2026-09-01, quando o login passou a validar
**usuário + senha** contra a tabela **`admin_users`**, com hash **bcrypt**
(`$2b$12$`, salt próprio por linha). `hmac.compare_digest` contra
`ADMIN_PASSWORD` **não é mais o caminho do login**.

| | |
|---|---|
| Tabela | `admin_users` (`app/models/admin.py`) — `username` único e normalizado em minúsculas, `password_hash`, `is_active`, `last_login_at` |
| Funções | `app/core/panel_auth.py` → `gerar_hash()`, `conferir_hash()`, `autenticar()` |
| Semeadura | `_semear_admin_inicial()` em `main.py`, chamado **no fim do `create_all`** |

**`ADMIN_PASSWORD` virou apenas a semente inicial**, e o seed só age com a
tabela **vazia**. Se já houver qualquer usuário, ele não toca em nada — senão
uma variável esquecida no `.env` ressuscitaria uma senha antiga depois de o
operador ter trocado a dele.

> ### ⚠️ ARMADILHA: o `ADMIN_PASSWORD` do `.env` está DESATUALIZADO
> Ele ainda guarda a senha **original**, não a que está valendo. Como o seed só
> roda com a tabela vazia, isso é inofensivo no dia a dia — **mas se a linha do
> `admin` for perdida, a próxima subida recria o usuário com a senha ANTIGA**,
> não com a atual. Quem for diagnosticar "minha senha parou de funcionar"
> precisa saber disso, ou vai concluir que o banco corrompeu.
>
> Mantido de propósito: **é hoje o único caminho de recuperação** se a linha
> sumir. Remover só quando existir tela de troca de senha no painel.

| Segredo | O que é | Se for perdido |
|---|---|---|
| `ADMIN_PASSWORD` | **semente inicial** do usuário `admin` (só usada com `admin_users` vazia) | gere outra no `.env`; para trocar a senha VIGENTE, ver o bloco de troca acima |
| `PANEL_SESSION_SECRET` | chave HMAC que assina o cookie de sessão (64 hex) | gere outra; **todos os logados caem** e refazem login. Nenhum dado é perdido |

Ambos vivem em `/home/ubuntu/atendit/.env` (600) e são injetados nos containers
pelo `docker-compose.yml`. A senha também está em
`/home/ubuntu/atendit-segredos/<timestamp>/ADMIN_PASSWORD.txt` (600).
⚠️ Guarde no gerenciador de senhas: os backups estão **na própria VPS**.

### Como funciona

- `GET /login` serve o formulário, com **dois campos** (usuário e senha);
  `POST /login` chama `panel_auth.autenticar()`, que confere o bcrypt gravado
  em `admin_users` e emite o cookie.
- **Usuário inexistente e senha errada devolvem a MESMA mensagem e gastam o
  MESMO tempo.** Mensagens distintas transformariam o login num oráculo de
  quais contas existem; e sem o bcrypt descartável que `autenticar()` roda para
  usuário inexistente, a resposta voltaria em microssegundos contra ~250ms — a
  diferença de tempo entregaria a mesma informação.
- Cookie **`atendit_painel`** = `<expiração_unix>.<hmac>`, com **HttpOnly**
  (invisível ao JavaScript, então XSS não rouba a sessão), **Secure** (só
  HTTPS), **SameSite=Lax** (sobrevive ao redirect de volta do OAuth do Google)
  e validade de **12h**.
- **Stateless**: não há tabela de sessões, então reiniciar a API não desloga
  ninguém. O preço é que `/logout` apaga o cookie do navegador mas **não o
  revoga no servidor** — um cookie copiado antes vale até expirar. Quem revoga
  tudo de uma vez é trocar `PANEL_SESSION_SECRET`.

### 🔴 Lista de proteção POR EXCEÇÃO — leia antes de mexer

`app/core/panel_auth.py` → `CAMINHOS_PROTEGIDOS`. **Só o que está nessa lista
exige sessão; todo o resto passa.**

O desenho inverso — "protege tudo, exceto estas públicas" — é mais seguro no
papel e catastrófico na prática: basta esquecer `/login` na lista de exceções
para trancar o acesso ao painel, sem caminho de volta pela web. Esta abordagem
falha para o lado de deixar algo desprotegido, que é visível e corrigível.

**Consequência assumida: rota nova nasce DESPROTEGIDA.** Quem acrescentar rota
de painel precisa acrescentá-la em `CAMINHOS_PROTEGIDOS`.

Protegidas hoje:

```
/dashboards/*          /v1/agent/config       /v1/ai/copilot
/v1/tenants/update     /v1/whatsapp/qrcode    /v1/whatsapp/pair-code
```

Públicas de propósito: `/`, `/login`, `/logout`, `/cadastro`, `/health`,
`/v1/auth/register`, `/webhook/*`, `/{tenant_slug}` (a página do painel) e
`/calendar/*`.

⚠️ **`/calendar/*` continua aberta** — inclui `POST /calendar/disconnect/{id}`,
que desconecta a agenda de um inquilino. Não estava no escopo da mudança; é o
próximo candidato natural à lista.

### Comportamento por tipo de requisição

O middleware distingue navegação de XHR pelo cabeçalho `Accept`:

| origem | resposta sem sessão |
|---|---|
| navegador (`text/html`) | **303** para `/login?next=<destino>` |
| `fetch()` / XHR | **401** JSON |

Redirecionar um XHR faria o painel injetar o HTML do login dentro da área de
conteúdo. O `loadView` do `index.html` trata o 401 mandando para `/login`.

### Se perder o acesso ao painel

O `.env` está na VPS e o acesso é por SSH: leia ou troque `ADMIN_PASSWORD`
com `sed -n 's/^ADMIN_PASSWORD=//p' /home/ubuntu/atendit/.env` e recrie o
container da API. **Não existe cenário em que o painel tranque o SSH** — é por
isso que a chave SSH continua sendo o acesso de última instância.
