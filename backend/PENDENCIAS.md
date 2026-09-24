# PENDÊNCIAS.md — itens abertos do roadmap

> Existe para que o que está em aberto **não dependa da memória de uma conversa**.
> Item concluído sai daqui e vira registro no documento do módulo
> (`RESTAURAR-AUTH.md`, `RUNBOOK-WHATSAPP.md`).
> Última atualização: **2026-09-01**.

---

## Pendências abertas

- [x] **Caminho `@lid` puro sem `remoteJidAlt`** — validado em **2026-09-24**.
  Testado com payload simulado de LID puro (`227027877662961@lid`) sem cache/sem alt.
  O sistema emite log de alerta `[WEBHOOK LID ALERTA]`, persiste o chat e mensagem
  no PostgreSQL/asyncpg utilizando sintaxe padrão SQL `CAST(... AS jsonb)` e, na recusa
  de despacho pela Evolution API, registra imediatamente `[EVOLUTION ERROR]` e
  `[WEBHOOK ERROR]`, eliminando qualquer falha silenciosa.

- [x] **Cota do Gemini e Conectividade de IA** — chave restabelecida em 2026-09-24, cadeia de contingência testada e operacional no modelo mais leve e econômico (`gemini-2.5-flash`).
  Estourou em 2026-09-01 durante os testes. Enquanto for free tier, qualquer
  bateria de teste consome a cota que o atendimento real precisaria. Avaliar
  billing antes de qualquer cliente.

  🟢 **Deixou de ser silencioso em 2026-09-01:** a cadeia agora avança para o
  próximo modelo quando um estoura a cota (a cota é *por modelo*), e se os três
  falharem o cliente recebe uma mensagem de contingência em vez de nada. O
  limite continua existindo — o que mudou é que ele não some mais o
  atendimento sem aviso.

---

## Também em aberto, herdado (não perder de vista)

- [x] **Senha do painel é temporária e fraca.** — mecanismo de alteração e recuperação autônomo implementado e validado em 2026-09-24.
  cliente real. Procedimento em `RESTAURAR-AUTH.md`, seção *"SENHA DO PAINEL
  É TEMPORÁRIA"*.
- [x] **Não existe tela de troca de senha no painel.** — resolvido em 2026-09-24 com modal de recuperação em `login.html` (`/v1/auth/esqueci-senha`) e endpoint autenticado `/v1/auth/alterar-senha` com hash bcrypt. Enquanto não existir,
  trocar senha exige SSH — e é por isso que `ADMIN_PASSWORD` continua no
  `.env` como único caminho de recuperação.
- [x] **Calendly** — suíte E2E executada e aprovada em **2026-09-24**.
  Validado via `app.tests.test_calendly_e2e`: validação de token na API v2,
  rejeição de token inválido (`TokenInvalido`), armazenamento seguro com AES
  (`calendar_connections.credentials_encrypted`), validação criptográfica de
  assinatura HMAC-SHA256 de webhook com rejeição de replay (> 300s), ingestão
  idempotente de eventos (`invitee.created` e `invitee.canceled`) e persistência
  espelhada na tabela `appointments`.

---

## Resolvido

- [x] **Autenticação da rota `POST /webhook/evolution/{tenant_id}`** —
  fechada em **2026-09-01** com segredo compartilhado + `hmac.compare_digest`.
  Header ausente ou errado ⇒ **401**; segredo não configurado no servidor ⇒
  **503** (falha fechado). Testes negativos e comando de reverificação em
  `RUNBOOK-WHATSAPP.md`, seção *"AUTENTICAÇÃO DO WEBHOOK"*.

  ⚠️ **O teste positivo de ciclo completo ficou incompleto**, por cota do
  Gemini esgotada no dia — não por causa da mudança. O que ficou **provado**:
  tráfego real do gateway atravessa a trava (HTTP 200, origem `172.19.0.5`), e
  os 4 testes negativos recusam sem processar nada. O que **falta medir**:
  uma pergunta real de ponta a ponta, incluindo o tempo do ciclo
  (referência anterior: **22,8s**).

- [x] **Conta de cliente, confirmação de e-mail e recuperação de senha** —
  fechado em **2026-09-01**. Tabela `tenant_users` (bcrypt reaproveitado de
  `panel_auth`), cadastro real em `tenants` + `tenant_users` numa única
  transação, confirmação de e-mail com token de uso único, login próprio
  (`/tenant/login`) com cookie separado do admin, e recuperação de senha com
  token de 1 hora. Documentado em `RESTAURAR-AUTH.md`, seção
  *"CONTAS DE CLIENTE (tenant_users)"*.

  **Encerra também o item de e-mail transacional (Resend)**: o transporte
  entrou em produção neste fluxo, com entrega confirmada (`last_event:
  delivered`) nos e-mails de confirmação e de recuperação.

  ⚠️ **Tenant novo nasce SEM WhatsApp e SEM calendário.** Conectar é passo
  manual do próprio cliente, no painel dele, depois do primeiro login — não faz
  parte do cadastro. É deliberado: criar instância da Evolution no cadastro
  geraria uma instância órfã por formulário abandonado, e cada uma nasce com a
  chave global da Evolution embutida como token (ver SEC-09).

- [x] **Divergência entre o `tenants.json` e a tabela `tenants`** — fechada em
  **2026-09-01**. As quatro rotas do painel admin (`GET /v1/tenants/`,
  `GET /v1/tenants/{slug}`, `POST /v1/tenants/update`, `POST /v1/agent/config`)
  passaram a ler e escrever no **Postgres**.

  **Migração antes do código:** os 5 slugs que só existiam no arquivo (`mani`,
  `smartinovat`, `teste`, `teste-10`, `empresa`) foram para `tenants`.
  `manitest` existia nos dois e foi **pulado, não sobrescrito** — o JSON o
  chamava de "MANITEST" e o banco de "SmarTInovaT", que é o nome real.

  **Nenhuma coluna nova foi criada.** `phone` e `ai_number` foram para
  `tenants.meta_data`; a config do agente para `ai_configs.agent_name` +
  `ai_configs.meta_data{tone,questions}`. ⚠️ `phone`/`ai_number` **não** foram
  para `tenants.whatsapp_number_e164` de propósito: aquela coluna é escrita
  pelo fluxo de pareamento, e gravar valor de formulário ali corromperia o
  estado real da instância.

  **O formato das respostas foi preservado**, então nenhuma tela precisou ser
  editada. O fallback de slug inexistente (objeto sintético com 200, em vez de
  404) foi **mantido** — verificado que `index.html` já trata resposta não-ok
  montando o mesmo objeto, então 404 também funcionaria; como as duas opções
  são equivalentes para o único consumidor, não mexi no contrato.

  **Testado:** tenant criado pelo `/cadastro` público apareceu em
  `GET /v1/tenants/` (7 inquilinos), foi editado por `POST /v1/tenants/update` e
  a alteração foi conferida direto no banco. `manitest` continua listado e
  editável (regravei os mesmos valores para provar o caminho sem alterar dado).

  **`tenants.json` foi esvaziado (`{}`) mas NÃO apagado**, e
  `get_tenants_store`/`save_tenants_store` agora **levantam** em vez de
  devolver vazio — um chamador esquecido tem de virar erro visível, não
  "nenhum inquilino", que é indistinguível de banco vazio. Grep em todo o app
  (Python, frontend, shell, compose) não achou nenhum chamador restante.

- [x] **Fallback do LLM em 429/503** — fechado em **2026-09-01**.
  `_deve_tentar_proximo()` faz a cadeia avançar em **404, 429 e 503**, não só
  em 404. O retry com backoff para 503 **no mesmo modelo** (3 tentativas,
  espera 5s e 10s) foi **preservado**: só depois de esgotá-lo é que se troca de
  modelo.

  Erro que **não** é de disponibilidade (payload inválido, chave errada, bug
  nosso) continua abortando no primeiro modelo — trocar de modelo não conserta
  e só gastaria cota repetindo a mesma falha.

  **O silêncio acabou:** quando a cadeia inteira falha, `_gerar_ou_contingenciar`
  devolve um texto de contingência que segue pelo **mesmo caminho de envio já
  testado**, em vez de levantar e cair no `[WEBHOOK FATAL]` sem mandar nada.

  **Testado com falhas forçadas:** 429 no 1º modelo → avança e responde pelo 2º;
  503 no 1º → idem; 400 → aborta no 1º (correto); **3 de 3 falhando → o cliente
  recebe a mensagem de contingência**, não silêncio. E um ciclo real, sem mock,
  continua concluindo em `[WEBHOOK SUCCESS]`.

- [x] **Isolamento por inquilino no painel** — fechado em **2026-09-01**.
  `/dashboards/*` e as rotas de configuração passaram a aceitar sessão de
  admin **ou** de tenant, com `autorizacao.exigir_acesso_ao_tenant()`
  conferindo o inquilino em 12 rotas. **10 testes de acesso cruzado, todos
  403**, com os dados do inquilino alvo conferidos intactos depois.

  **Três buracos pré-existentes fecharam junto**, todos anônimos e todos
  medidos antes da correção: `GET /v1/tenants/` (nome e e-mail de todos os
  inquilinos), `GET /rag/export/...` (baixei um documento real de um cliente,
  254 bytes) e `/calendar/status|disconnect|appointments` (agendas conectadas
  legíveis, e `disconnect` derrubaria a agenda de qualquer cliente). O item
  de `/calendar/*` que estava aberto nesta lista foi absorvido aqui.

  Documentado em `RESTAURAR-AUTH.md`, seção *"DUAS SESSÕES, UMA REGRA DE
  ISOLAMENTO"*, com a tabela de quais rotas conferem e de onde vem o alvo.
