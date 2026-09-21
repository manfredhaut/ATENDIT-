import hmac
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.tenant import AIConfig, Tenant
from app.services.evolution_service import evolution_service
from app.services.llm_service import llm_service
from app.services.rag_service import rag_service
from app.services.scheduling_tools import construir_ferramentas, instrucao_de_contexto
from app.services import waitlist_service

logger = logging.getLogger("atendit.webhook")

router = APIRouter(prefix="/webhook", tags=["WhatsApp Webhook Gateway"])


def _numero_destino(remote_jid: str, remote_jid_alt: Optional[str] = None) -> str:
    """
    Converte o identificador do interlocutor no numero aceito pelo /message/sendText.

    O WhatsApp passou a usar LID (Linked Device Identity) para contas com
    privacidade de numero: o remoteJid vem como '227027877662961@lid' — as vezes
    com sufixo de dispositivo (':53') — e o numero real chega em 'remoteJidAlt'.
    """
    origem = remote_jid or ""
    if remote_jid_alt and "@lid" not in remote_jid_alt:
        origem = remote_jid_alt  # o alternativo ja traz o numero real

    numero = origem.split("@", 1)[0]
    numero = numero.split(":", 1)[0]  # descarta sufixo de dispositivo (':53')
    return numero


def _formato_jid(remote_jid: str) -> str:
    """Rotulo do formato recebido, para diagnostico no log."""
    if "@lid" in remote_jid:
        return "lid"
    if "@g.us" in remote_jid:
        return "grupo"
    if "@s.whatsapp.net" in remote_jid:
        return "whatsapp.net"
    return "desconhecido"


# Reconhecimento de confirmacao da lista de espera.
# DETERMINISTICO de proposito, antes da IA: a oferta tem prazo, e depender do
# modelo para um "sim" gastaria cota, adicionaria ~15s de latencia e poderia
# falhar justo no caso sensivel a tempo. So dispara se houver oferta pendente
# DENTRO do prazo para aquele telefone, o que estreita muito o falso positivo.
CONFIRMACOES = {
    "sim", "s", "confirmo", "confirmar", "confirmado", "quero", "aceito",
    "pode", "pode marcar", "pode sim", "ok", "okay", "isso", "fechado",
    "beleza", "claro", "com certeza", "sim quero", "quero sim",
}


def _normalizar(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (texto or "").strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return "".join(c for c in t if c.isalnum() or c.isspace()).strip()


def _e_confirmacao(texto: str) -> bool:
    n = _normalizar(texto)
    return bool(n) and n in CONFIRMACOES


def _extract_message_text(data: Dict[str, Any]) -> Optional[str]:
    """Extrai com segurança o corpo de texto da mensagem em múltiplos formatos da Evolution API."""
    message = data.get("message") or {}

    # 1. Mensagem de texto simples
    if "conversation" in message and message["conversation"]:
        return str(message["conversation"])

    # 2. Mensagem com formatação estendida
    if "extendedTextMessage" in message:
        text = message["extendedTextMessage"].get("text")
        if text:
            return str(text)

    # 3. Legenda em imagem ou documento
    if "imageMessage" in message and message["imageMessage"].get("caption"):
        return str(message["imageMessage"]["caption"])

    # 4. Campo direto
    if "messageText" in data and data["messageText"]:
        return str(data["messageText"])

    return None


async def _async_process_incoming_message(
    tenant_id: uuid.UUID,
    instance_name: str,
    remote_jid: str,
    user_message: str,
    remote_jid_alt: Optional[str] = None,
):
    """
    Processador de segundo plano para RAG, Inferência LLM e Resposta WhatsApp.
    Executa com sessão própria isolada de banco de dados.
    """
    logger.info(
        f"[WEBHOOK BACKGROUND] Processando mensagem para Tenant {tenant_id} "
        f"de '{remote_jid}': '{user_message[:50]}...'"
    )

    async with AsyncSessionLocal() as session:
        try:
            # 1. Carrega dados do Tenant e Configuração de IA
            stmt = (
                select(Tenant)
                .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            )
            result = await session.execute(stmt)
            tenant = result.scalar_one_or_none()

            if not tenant:
                logger.warning(f"[WEBHOOK ERROR] Tenant {tenant_id} inativo ou inexistente.")
                return

            ai_config: Optional[AIConfig] = tenant.ai_config

            # 2. Parâmetros de IA
            custom_key = ai_config.api_key if (ai_config and ai_config.api_key) else settings.GEMINI_API_KEY
            system_instruction = (
                ai_config.system_instruction
                if (ai_config and ai_config.system_instruction)
                else f"Você é o atendente virtual '{ai_config.agent_name if ai_config else 'ManiBot'}' da empresa {tenant.name}. Responda de forma cortês, profissional e concisa."
            )
            model_name = ai_config.model if ai_config else "gemini-2.5-flash"
            temperature = ai_config.temperature if ai_config else 0.7

            # 2b. Atalho da lista de espera: "sim" respondendo a uma oferta.
            numero_cliente = _numero_destino(remote_jid, remote_jid_alt)
            if _e_confirmacao(user_message):
                pendente = await waitlist_service.oferta_pendente(tenant_id, numero_cliente)
                if pendente is not None:
                    logger.info(
                        f"[WAITLIST] Confirmação reconhecida de {numero_cliente} "
                        f"para {pendente['offered_start'].isoformat()}."
                    )
                    r = await waitlist_service.confirmar_oferta(tenant_id, numero_cliente)
                    if r.get("confirmado"):
                        from app.services import template_service
                        texto = await template_service.render(
                            tenant_id=tenant_id,
                            template_type="confirmation",
                            nome=pendente.get("customer_name"),
                            servico=r["servico"],
                            horario=r["reserva"]["rotulo"],
                        )
                    elif r.get("motivo") == "horario_tomado":
                        texto = (
                            "Que pena — esse horário acabou de ser preenchido. "
                            "Você continua na lista de espera e eu aviso no próximo que abrir."
                        )
                    else:
                        texto = "Não consegui confirmar agora. Pode tentar de novo em instantes?"
                    await evolution_service.send_text_message(
                        instance_name=instance_name or tenant.slug,
                        recipient_number=numero_cliente,
                        text=texto,
                    )
                    logger.info(f"[WAITLIST] Fluxo de confirmação concluído: {r.get('confirmado')}")
                    return

            # 3. Busca semântica de contexto RAG
            context_chunks = await rag_service.search_relevant_context(
                db=session,
                tenant_id=tenant_id,
                query_text=user_message,
                custom_api_key=custom_key,
                limit=3,
            )

            # 4. Ferramentas de agenda. O telefone e o inquilino ficam presos
            #    nos handlers: o modelo nao os recebe e nao pode inventa-los.
            declaracoes, handlers = construir_ferramentas(
                tenant_id=tenant_id,
                customer_phone=numero_cliente,
            )

            # 5. Geração de resposta fundamentada com o Google Gemini
            ai_reply = await _gerar_ou_contingenciar(
                system_prompt=(system_instruction or "") + instrucao_de_contexto(),
                user_message=user_message,
                context_chunks=context_chunks,
                custom_api_key=custom_key,
                model=model_name,
                temperature=temperature,
                tools=declaracoes,
                tool_handlers=handlers,
            )

            # 6. Despacho da resposta de volta para o cliente via Evolution API
            recipient_number = numero_cliente
            sent = await evolution_service.send_text_message(
                instance_name=instance_name or tenant.slug,
                recipient_number=recipient_number,
                text=ai_reply,
            )

            if sent:
                logger.info(
                    f"[WEBHOOK SUCCESS] Ciclo completo concluído com sucesso para Tenant {tenant_id} -> {recipient_number}"
                )
            else:
                logger.error(
                    f"[WEBHOOK ERROR] Falha no despacho da resposta via Evolution API para {recipient_number}"
                )

        except Exception as e:
            logger.error(
                f"[WEBHOOK FATAL] Erro inesperado durante o processamento em background: {str(e)}"
            )


# ---------------------------------------------------------------------------
# Contingencia: cliente NUNCA fica sem resposta (2026-09-01)
#
# Antes, qualquer falha do LLM caia no [WEBHOOK FATAL] e o cliente ficava sem
# NADA -- indistinguivel, para ele, de um numero abandonado. Silencio e a pior
# resposta possivel: ele reenvia, espera, e desiste.
# ---------------------------------------------------------------------------
TEXTO_CONTINGENCIA = (
    "Estamos com instabilidade momentânea no atendimento automático. "
    "Tente novamente em alguns minutos ou aguarde que um atendente humano responda."
)


async def _gerar_ou_contingenciar(**kwargs) -> str:
    """
    Chama o LLM; se a cadeia inteira falhar, devolve o texto de contingência.

    Devolve TEXTO em vez de levantar, de proposito: assim o passo de envio
    logo abaixo roda igual, e a mensagem sai pelo mesmo caminho ja testado.

    Falha de disponibilidade do provedor vira aviso ao cliente. Qualquer OUTRO
    erro tambem: do ponto de vista de quem esta do outro lado do WhatsApp, bug
    nosso e provedor fora sao a mesma coisa -- ninguem respondeu. A distincao
    fica no LOG, onde ela serve para alguem agir.
    """
    from app.services.llm_service import TodosModelosIndisponiveis

    try:
        return await llm_service.generate_response(**kwargs)
    except TodosModelosIndisponiveis as exc:
        logger.error(f"[LLM CONTINGENCIA] Cadeia inteira indisponível: {exc}. Enviando aviso ao cliente.")
    except Exception as exc:
        logger.error(f"[LLM CONTINGENCIA] Falha inesperada no LLM: {exc}. Enviando aviso ao cliente.")
    return TEXTO_CONTINGENCIA


@router.post("/evolution/{tenant_id}", status_code=status.HTTP_200_OK)
async def handle_evolution_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Recepção assíncrona de webhooks do Gateway Evolution API v2.
    Filtra eventos, descarta loops de auto-resposta e enfileira o processamento.
    """
    # ---- AUTENTICACAO DO WEBHOOK (2026-09-01) ---------------------------
    # Ate hoje esta rota aceitava QUALQUER POST, sem nenhum controle: quem
    # soubesse o tenant_id injetava mensagens como se fosse cliente, gastava
    # cota do Gemini e disparava WhatsApp para o numero que quisesse. A
    # documentacao afirmava que havia um "mecanismo proprio (WEBHOOK_SECRET)";
    # o nome so existia dentro de um comentario.
    #
    # O gateway devolve o segredo no header X-Webhook-Secret, configurado no
    # campo "headers" do webhook/set. MEDIDO em 2026-09-01: o Evolution monta
    # o cliente HTTP com axios.create({baseURL, headers}) e envia os headers
    # em TODA chamada -- confirmado no log com header_presente=True,
    # tamanho=64, confere=True antes de esta trava entrar.
    #
    # DECISOES, e o porque de cada uma:
    #
    #   header AUSENTE      -> 401. Ausencia e tratada como invalida, NUNCA
    #                          como "sem verificacao, deixa passar". Um
    #                          gateway mal configurado deve falhar de forma
    #                          ruidosa; a alternativa reabre o buraco inteiro
    #                          no dia em que alguem recadastrar o webhook sem
    #                          o header.
    #
    #   segredo NAO         -> 503, nao 401 e nao "libera". Distingue erro de
    #   CONFIGURADO no          CONFIGURACAO de credencial recusada, que sao
    #   servidor                problemas de pessoas diferentes. Mesmo padrao
    #                          ja usado pelo INTERNAL_API_TOKEN. Comparar
    #                          contra string vazia deixaria header vazio
    #                          "bater" -- transformaria falha de config em
    #                          porta aberta.
    #
    #   comparacao          -> hmac.compare_digest, tempo constante, mesmo
    #                          padrao do login do painel. Um "==" vazaria o
    #                          prefixo correto pelo tempo de resposta.
    #
    # A trava vem ANTES de ler o corpo: payload de origem nao autenticada nao
    # deve nem ser desserializado.
    esperado = settings.WEBHOOK_SECRET or ""
    if not esperado:
        logger.error(
            "[WEBHOOK AUTH] WEBHOOK_SECRET nao configurado no servidor. "
            "Recusando com 503 -- a rota FECHA quando mal configurada, nao abre."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook indisponivel: segredo nao configurado no servidor.",
        )

    recebido = request.headers.get("X-Webhook-Secret")
    if recebido is None or not hmac.compare_digest(recebido, esperado):
        logger.warning(
            f"[WEBHOOK AUTH] Recusado para Tenant {tenant_id}: "
            f"motivo={'header_ausente' if recebido is None else 'segredo_invalido'} "
            f"origem={request.client.host if request.client else '?'}. "
            f"Payload NAO foi processado."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook nao autorizado.",
        )
    # ---------------------------------------------------------------------
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        logger.error(f"[WEBHOOK ERROR] Payload JSON inválido recebido para Tenant {tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido."
        )

    event = str(body.get("event", "")).lower()
    instance_name = body.get("instance", "")
    data = body.get("data") or {}

    logger.debug(f"[WEBHOOK INCOMING] Evento '{event}' recebido para Tenant {tenant_id}")

    # Processa estritamente eventos de novas mensagens (upsert)
    if "messages.upsert" not in event and event != "messages_upsert":
        logger.info(
            f"[WEBHOOK DESCARTE] motivo=evento_nao_tratado evento='{event}' "
            f"instancia='{instance_name}'"
        )
        return {
            "status": "ignored",
            "reason": f"Evento '{event}' não requer processamento de resposta."
        }

    # Validação do emissor: descarta mensagens enviadas pelo próprio bot (previne loop infinito)
    key = data.get("key") or {}
    if key.get("fromMe", False) is True:
        logger.info(
            f"[WEBHOOK DESCARTE] motivo=fromMe remoteJid='{key.get('remoteJid','')}' "
            f"instancia='{instance_name}' (mensagem enviada pelo proprio aparelho pareado)"
        )
        return {
            "status": "ignored",
            "reason": "Mensagem emitida pelo próprio sistema (fromMe: true)."
        }

    remote_jid = key.get("remoteJid", "")
    remote_jid_alt = key.get("remoteJidAlt") or None
    # Ignora mensagens de status e broadcast
    if not remote_jid or "status@broadcast" in remote_jid:
        logger.info(
            f"[WEBHOOK DESCARTE] motivo=broadcast_ou_vazio remoteJid='{remote_jid}' "
            f"instancia='{instance_name}'"
        )
        return {
            "status": "ignored",
            "reason": "Mensagem de broadcast/status ignorada."
        }

    # Extração do conteúdo de texto
    user_text = _extract_message_text(data)
    if not user_text or not user_text.strip():
        logger.info(
            f"[WEBHOOK DESCARTE] motivo=sem_texto remoteJid='{remote_jid}' "
            f"tipo='{data.get('messageType','?')}' instancia='{instance_name}'"
        )
        return {
            "status": "ignored",
            "reason": "Mensagem sem conteúdo textual processável."
        }

    logger.info(
        f"[WEBHOOK QUEUE] Enfileirando atendimento para Tenant {tenant_id} "
        f"de '{remote_jid}' (formato={_formato_jid(remote_jid)}, "
        f"destino={_numero_destino(remote_jid, remote_jid_alt)}): '{user_text[:40]}...'"
    )

    # Despacha o processamento pesado (RAG + LLM + Envio) para BackgroundTasks
    background_tasks.add_task(
        _async_process_incoming_message,
        tenant_id=tenant_id,
        instance_name=instance_name,
        remote_jid=remote_jid,
        user_message=user_text.strip(),
        remote_jid_alt=remote_jid_alt,
    )

    return {
        "status": "received",
        "tenant_id": str(tenant_id),
        "message": "Evento aceito e enfileirado para processamento."
    }
