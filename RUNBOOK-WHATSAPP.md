# RUNBOOK-WHATSAPP.md — operação da instância Evolution

> Documento operacional. **Nunca** registrar aqui o VALOR de nenhum segredo.
> Última atualização: **2026-09-01**.

---

## 🔴 RECRIOU A INSTÂNCIA? O WEBHOOK FOI JUNTO. RECADASTRE.

**Esta é a armadilha mais cara deste módulo, porque ela falha em silêncio.**

Toda vez que a instância do WhatsApp for **apagada e recriada** — o que acontece
sempre que se refaz o pareamento por QR do zero — o webhook registrado nela
**é apagado junto pelo banco**, e ninguém avisa.

### Por que acontece (medido, não suposto)

```
Webhook_instanceId_fkey   FOREIGN KEY ("instanceId") REFERENCES "Instance"(id)
                          ON DELETE CASCADE
```

`DELETE /instance/delete/{nome}` remove a linha de `Instance`; o `CASCADE`
leva a linha de `Webhook` atrás. A instância nova nasce **sem webhook nenhum**.

### Como o sistema se comporta sem webhook — leia com atenção

**Tudo parece funcionar.** É esse o problema:

| O que você observa | Estado real |
|---|---|
| `connectionStatus = open` | ✅ verdadeiro, o número está conectado |
| A mensagem do cliente aparece na tabela `Message` | ✅ verdadeiro, a Evolution recebeu e guardou |
| O painel abre, a API responde, `/health` = 200 | ✅ verdadeiro, e irrelevante |
| **O cliente recebe resposta** | 🔴 **NÃO. Nunca chega na API.** |

**Não há erro em lugar nenhum.** O log da API não registra nada porque nada
chega até ela; o log do gateway não registra nada porque não houve tentativa de
entrega. Procurar a falha em RAG, Gemini, cota ou envio é perder tempo: o
evento nunca saiu do gateway.

> **Como isso foi descoberto (2026-09-01):** um teste do próprio usuário, pelo
> **WhatsApp pessoal dele**, ficou sem resposta. **Não houve incidente com
> cliente real.** A instância havia sido recriada em 31/08 21:14 para refazer o
> pareamento, e o webhook foi embora no `CASCADE` sem que ninguém notasse.

### Diagnóstico em um comando

```bash
docker exec atendit-db psql -U atendit_user -d evolution -c 'SELECT enabled, url, events FROM "Webhook";'
```

**0 linhas = é isto.** Vá direto para o recadastro abaixo.

### Recadastro

```bash
ssh -i ssh-key-2026-06-11.key ubuntu@129.158.42.206
KEY=$(docker exec atendit-api python -c 'from app.core.config import settings; print(settings.EVOLUTION_API_KEY)')
docker exec -i atendit-api python - "$KEY" <<'FIM'
import sys, json, urllib.request
INST = "atendit_manitest"                                  # nome da instância
TENANT = "21e7ca95-2a64-48e9-9882-ddfb3cc62cba"            # id do inquilino
URL = f"http://atendit-api:8000/webhook/evolution/{TENANT}"
from app.core.config import settings                        # <- para o segredo
req = urllib.request.Request(
    f"http://evolution-api:8080/webhook/set/{INST}",
    data=json.dumps({"webhook": {
        "enabled": True, "url": URL,
        "webhookByEvents": False, "webhookBase64": False,
        "events": ["MESSAGES_UPSERT"],
        # OBRIGATORIO. Sem este header a rota devolve 401 e o atendimento
        # nao funciona. Ver "AUTENTICACAO" mais abaixo.
        "headers": {"X-Webhook-Secret": settings.WEBHOOK_SECRET,
                    "Content-Type": "application/json"},
    }}).encode(),
    headers={"Content-Type": "application/json", "apikey": sys.argv[1]})
with urllib.request.urlopen(req, timeout=60) as r:
    print(json.loads(r.read().decode()))
FIM
```

#### As três decisões embutidas nesse comando

**1. URL interna (`http://atendit-api:8000`), NUNCA o domínio público.**
O gateway alcança a API pelo DNS do Docker em ~300ms. Pelo domínio, a
requisição sai da VPS e volta — e a Cloudflare responde **erro 1010** a
requisições da VPS para o próprio domínio. Webhook apontando para
`https://atendit.smartinovat.com/...` **não funciona**, e falha em silêncio.

**2. Só `MESSAGES_UPSERT`.** É o único evento que a rota trata; todo o resto ela
descarta com `[WEBHOOK DESCARTE] motivo=evento_nao_tratado`. Assinar a lista
inteira só faz o gateway martelar a API com trabalho jogado fora.

**3. O `tenant_id` vai na URL.** É assim que a rota sabe de quem é a mensagem.
Uma instância por inquilino ⇒ **um recadastro por inquilino**.

**4. O header `X-Webhook-Secret` é OBRIGATÓRIO.** Desde 2026-09-01 a rota
recusa com **401** qualquer chamada sem ele. **Recadastrar sem o header
significa atendimento parado** — e, como sempre neste módulo, parado em
silêncio: a Evolution recebe a mensagem, tenta entregar, leva 401, e o cliente
nunca é respondido. Se você seguir o comando acima como está, isso não
acontece.

### Verificação — não pule, e não aceite log como prova

```bash
docker exec atendit-db psql -U atendit_user -d evolution -c 'SELECT enabled, url, events FROM "Webhook";'
```

Depois, prova de entrega ponta a ponta: mande uma mensagem real **de outro
número** e acompanhe

```bash
docker logs -f --since 2m atendit-api 2>&1 | grep -E "WEBHOOK|LLM|EVOLUTION"
```

O ciclo completo termina em `[WEBHOOK SUCCESS]`. Medido em 2026-09-01, com tudo
quente: **22,8s** do enfileiramento ao envio.

> ### ⚠️ DUAS ARMADILHAS DE MEDIÇÃO, ambas custaram tempo em 2026-09-01
>
> **1. Enviar uma mensagem pela API NÃO testa `MESSAGES_UPSERT`.** Envio emite
> **`SEND_MESSAGE`**. Se você assinou só `MESSAGES_UPSERT`, a sonda não gera
> entrega nenhuma — e você conclui que o webhook está quebrado quando ele está
> correto. Para provar a entrega sem um segundo telefone, assine
> `SEND_MESSAGE` **temporariamente**, faça a sonda, e **remova depois**.
>
> **2. Ausência de log de webhook no gateway NÃO é prova de nada.** O
> `webhook.controller` só loga se `LOG_LEVEL` contiver `WEBHOOKS`, e não contém.
> A prova de entrega é o **POST chegando na API**, não o silêncio do gateway.

### Primeira medição depois de reiniciar a API vem inflada

Cold start do pool do asyncpg custa ~16s na primeira requisição. A mesma
pergunta mediu **32,4s** a frio e **22,8s** quente. **Meça a segunda**, não a
primeira, ou você vai perseguir uma lentidão que não existe.

---

## Estado de referência (2026-09-01)

| | |
|---|---|
| Instância | `atendit_manitest`, `connectionStatus=open` |
| Inquilino | `21e7ca95-2a64-48e9-9882-ddfb3cc62cba` (slug `manitest`) |
| Webhook | `enabled=true`, `["MESSAGES_UPSERT"]`, URL interna |
| Rota que recebe | `POST /webhook/evolution/{tenant_id}` — pública porque o gateway não tem como enviar `X-Internal-Token`, e **sem nenhum controle no lugar disso**; ver a seção final deste documento |
| Credenciais da sessão | tabela `Session` do banco `evolution` (~3 KB) ⇒ `POST /instance/restart/{nome}` **não** exige novo QR |

⚠️ **`docker rm` no gateway é diferente de recriar a instância.** O container
pode ser recriado à vontade: as credenciais estão no Postgres. O que mata o
webhook é apagar a **instância** (`DELETE /instance/delete/{nome}`).

---

## Pendência conhecida deste módulo

**Caminho `@lid` puro, sem `remoteJidAlt` resolvido — NÃO TESTADO.**
Ver `backend/PENDENCIAS.md`. O WhatsApp passou a identificar remetentes por
`<numero>@lid` em vez do JID de telefone. Em 2026-09-01 a mensagem chegou como
`227027877662961@lid` e o sistema resolveu o telefone corretamente — **mas por
cache**, e o teste cronometrado usou o JID de telefone, não o `@lid`. O
comportamento com um `@lid` sem mapeamento em cache **é desconhecido**.

---

## ✅ AUTENTICAÇÃO DO WEBHOOK — resolvido em 2026-09-01

**Era:** a rota `POST /webhook/evolution/{tenant_id}` aceitava **qualquer** POST,
sem nenhum controle. A documentação afirmava que ela usava `WEBHOOK_SECRET`;
o nome só existia dentro de um comentário em `app/core/security.py`, sem
nenhuma implementação. Medido antes da correção: **HTTP 200** pelo caminho
público, sem cabeçalho nenhum, inclusive com `tenant_id` inexistente.

**É:** segredo compartilhado de 64 hex (`openssl rand -hex 32`, mesmo padrão do
`INTERNAL_API_TOKEN`), guardado em `.env` (600) e injetado nos três serviços
Python pelo `docker-compose.yml`. A Evolution o devolve no header
`X-Webhook-Secret` a cada chamada, via o campo `headers` do `webhook/set`. A
rota compara com `hmac.compare_digest` **antes de ler o corpo**.

### Comportamento definido, e o porquê de cada escolha

| Situação | Resposta | Por quê |
|---|---|---|
| Header correto | seguem as regras de sempre | — |
| Header com valor errado | **401** | — |
| **Header AUSENTE** | **401** | Ausência é **inválida**, nunca "sem verificação, deixa passar". Um gateway mal configurado tem de falhar ruidosamente; o contrário reabre o buraco inteiro no dia em que alguém recadastrar o webhook sem o header |
| `WEBHOOK_SECRET` **não configurado no servidor** | **503** | Distingue erro de **configuração** de credencial **recusada** — são problemas de pessoas diferentes. E comparar contra string vazia faria um header vazio "bater", transformando falha de config em porta aberta. Mesmo padrão do `INTERNAL_API_TOKEN` |

Comparação em **tempo constante** (`hmac.compare_digest`, o mesmo do login do
painel): um `==` vazaria o prefixo correto pelo tempo de resposta.

### A Evolution envia o header em toda chamada? SIM — medido, não suposto

O `webhook.controller` monta o cliente HTTP com
`axios.create({ baseURL, headers: webhookHeaders })`, então os headers vão em
**toda** entrega. Confirmado empiricamente **antes** de ligar a trava, com a
rota ainda aberta e instrumentada:

```
[WEBHOOK AUTH OBS] header_presente=True tamanho=64 confere=True
```

> **Por que a instrumentação veio antes da trava:** ligar a verificação e só
> então descobrir que o header não chega derrubaria o atendimento inteiro —
> em silêncio, que é o modo de falha que este módulo inteiro existe para
> eliminar. Prova primeiro, trava depois.

### Teste negativo — para quem precisar reverificar no futuro

```bash
D=atendit.smartinovat.com
T=21e7ca95-2a64-48e9-9882-ddfb3cc62cba
PAY='{"event":"messages.upsert","instance":"atendit_manitest","data":{"key":{"remoteJid":"5585989020465@s.whatsapp.net","fromMe":false,"id":"TESTE"},"message":{"conversation":"teste"},"messageTimestamp":1788270000}}'

# 1. sem header  -> tem de dar 401
curl -s -o /dev/null -w 'sem header: %{http_code}\n' -X POST "https://$D/webhook/evolution/$T" \
     -H 'Content-Type: application/json' -d "$PAY"

# 2. header errado -> tem de dar 401
curl -s -o /dev/null -w 'errado:     %{http_code}\n' -X POST "https://$D/webhook/evolution/$T" \
     -H 'Content-Type: application/json' -H 'X-Webhook-Secret: 000000' -d "$PAY"

# 3. tenant inexistente, sem header -> tem de dar 401
curl -s -o /dev/null -w 'tenant 404: %{http_code}\n' -X POST \
     "https://$D/webhook/evolution/00000000-0000-0000-0000-000000000000" \
     -H 'Content-Type: application/json' -d "$PAY"
```

**401 não basta como prova.** Confirme que nada foi processado:

```bash
docker logs --since 60s atendit-api 2>&1 | grep -c 'RAG SEARCH'      # tem de ser 0
docker logs --since 60s atendit-api 2>&1 | grep -c 'LLM INFERENCE'   # tem de ser 0
docker logs --since 60s atendit-api 2>&1 | grep -c 'EVOLUTION DISPATCH'  # tem de ser 0
```

Resultado em 2026-09-01: **4 recusas, 401 em todas, e `RAG=0 LLM=0 ENVIO=0`**,
com `[WEBHOOK AUTH] ... Payload NAO foi processado.` em cada uma.

### Custo da trava

Medido, mediana de 5 chamadas: **14,0 ms** no caminho de aceite, **25,1 ms** na
recusa. A verificação roda antes de desserializar o corpo, então uma recusa
não custa nem parsing nem chamada ao Gemini (**0** chamadas a
`generativelanguage` na janela dos testes negativos).

---
