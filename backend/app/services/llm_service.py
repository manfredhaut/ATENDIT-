import asyncio
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger("atendit.llm_service")

# Teto de idas e voltas modelo <-> ferramenta numa unica resposta ao cliente.
# Existe para que uma funcao que devolve algo que o modelo nao aceita nao vire
# laco infinito consumindo cota da API.
MAX_ITERACOES_FERRAMENTAS = 5

# Retentativa para 503 UNAVAILABLE ("high demand"), que e TRANSITORIO.
# Distincao que importa:
#   503 -> espera e tenta DE NOVO NO MESMO MODELO (aqui)
#   404 -> troca de modelo (cadeia de fallback, mais abaixo)
# Trocar de modelo por causa de 503 mascararia sobrecarga do provedor e
# mandaria trafego para um modelo que o inquilino nao escolheu.
TENTATIVAS_503 = 3
ESPERA_503_SEGUNDOS = [5, 10]  # entre a 1a->2a e a 2a->3a tentativa


class ModeloIndisponivel(RuntimeError):
    """
    O modelo pedido existe no catalogo mas nao esta liberado para este projeto.

    Erro tipico: 404 'no longer available to new users'. Projeto novo do Google
    Cloud nao recebe acesso a modelos ja depreciados, e a listagem /models
    CONTINUA mostrando o modelo — listagem nao e autoridade sobre acesso.
    E o unico erro que justifica tentar o proximo modelo da cadeia: 429 (cota)
    e 503 (sobrecarga) sao transitorios e trocar de modelo por causa deles
    mascararia o problema real.
    """


def _e_sobrecarga_transitoria(mensagem: str) -> bool:
    m = mensagem.lower()
    return "503" in m or "unavailable" in m or "overloaded" in m


class TodosModelosIndisponiveis(RuntimeError):
    """
    Toda a cadeia de modelos falhou por indisponibilidade (404/429/503).

    Tem tipo proprio para o chamador poder distinguir "o provedor esta fora"
    de "o codigo tem um bug" -- respostas diferentes para o cliente.
    """


def _e_cota_esgotada(mensagem: str) -> bool:
    """429 RESOURCE_EXHAUSTED — cota da API estourada."""
    m = mensagem.lower()
    return "429" in m or "resource_exhausted" in m or "quota" in m


def _deve_tentar_proximo(mensagem: str) -> tuple:
    """
    Decide se vale trocar de modelo, e devolve (bool, motivo).

    Os TRES casos abaixo significam "este modelo nao vai responder AGORA", e a
    cadeia de reserva existe exatamente para isso:

      404 -> o modelo nao existe para este projeto (nunca vai existir)
      429 -> cota estourada. A cota do Gemini e
             GenerateRequestsPerDayPerProjectPerModel, ou seja, POR MODELO:
             o proximo da cadeia tem saldo proprio e independente.
      503 -> sobrecarga. So chega aqui DEPOIS de o retry no mesmo modelo
             (TENTATIVAS_503) ter se esgotado -- ver _chamar_com_retry.

    Ate 2026-09-01 so o 404 avancava, e um 429 no primeiro modelo abortava o
    atendimento inteiro com dois modelos de reserva intactos ao lado.
    """
    if _e_modelo_indisponivel(mensagem):
        return True, "404 indisponivel neste projeto"
    if _e_cota_esgotada(mensagem):
        return True, "429 cota esgotada"
    if _e_sobrecarga_transitoria(mensagem):
        return True, "503 sobrecarregado apos as retentativas"
    return False, ""


def _e_modelo_indisponivel(mensagem: str) -> bool:
    m = mensagem.lower()
    if "404" not in m and "not_found" not in m:
        return False
    return "no longer available" in m or "not found" in m


class LLMService:
    """Serviço de integração com Google Gemini utilizando o SDK oficial google-genai."""

    def __init__(self, default_api_key: Optional[str] = None):
        self.default_api_key = default_api_key or settings.GEMINI_API_KEY

    def _get_client(self, custom_api_key: Optional[str] = None) -> genai.Client:
        """Instancia o cliente oficial do Google GenAI com a chave mais prioritária."""
        key = custom_api_key or self.default_api_key
        if not key:
            logger.error("[LLM ERROR] Nenhuma chave de API do Gemini foi fornecida ou configurada no ambiente.")
            raise ValueError("Chave de API do Gemini não configurada.")
        return genai.Client(api_key=key)

    def _cadeia_de_modelos(self, pedido: Optional[str]) -> List[str]:
        """
        Monta a ordem de tentativa: o modelo pedido primeiro, depois os de
        reserva, sem repetir.

        O pedido vem de ai_configs.model. Quando ele funciona, os de reserva
        nunca são tocados — a cadeia é rede de segurança, não roteamento.
        """
        cadeia: List[str] = []
        for candidato in [pedido or settings.DEFAULT_GEMINI_MODEL] + settings.modelos_fallback:
            if candidato and candidato not in cadeia:
                cadeia.append(candidato)
        return cadeia

    async def _executar_ferramenta(
        self,
        nome: str,
        argumentos: Dict[str, Any],
        tool_handlers: Dict[str, Callable],
    ) -> Dict[str, Any]:
        """
        Executa localmente a função pedida pelo modelo e devolve o resultado
        já no formato de dicionário que o Gemini espera receber de volta.

        Nunca levanta exceção: uma falha da ferramenta volta ao modelo como
        conteúdo de erro, para que ele possa se recuperar ou avisar o cliente.
        """
        handler = tool_handlers.get(nome)
        if handler is None:
            logger.error(f"[LLM TOOL] Modelo pediu a função '{nome}', que não tem handler registrado.")
            return {"erro": f"A função '{nome}' não está disponível."}

        try:
            if inspect.iscoroutinefunction(handler):
                resultado = await handler(**argumentos)
            else:
                resultado = handler(**argumentos)
            logger.info(f"[LLM TOOL] Função '{nome}' executada com sucesso. Args: {argumentos}")
            return resultado if isinstance(resultado, dict) else {"resultado": resultado}
        except Exception as exc:
            logger.error(f"[LLM TOOL FAILED] Erro ao executar a função '{nome}': {exc}")
            return {"erro": f"Falha ao executar '{nome}': {exc}"}

    async def _chamar_com_retry(self, client, modelo: str, contents, config):
        """
        Uma chamada ao modelo, com retentativa apenas para 503.

        404 NAO e retentado aqui: sobe imediatamente para a cadeia de
        fallback trocar de modelo. Retentar 404 seria esperar 15s para
        receber o mesmo 404.
        """
        ultimo = None
        for tentativa in range(1, TENTATIVAS_503 + 1):
            try:
                return await client.aio.models.generate_content(
                    model=modelo, contents=contents, config=config
                )
            except Exception as exc:
                mensagem = str(exc)
                if _e_modelo_indisponivel(mensagem) or not _e_sobrecarga_transitoria(mensagem):
                    raise
                ultimo = exc
                if tentativa < TENTATIVAS_503:
                    espera = ESPERA_503_SEGUNDOS[min(tentativa - 1, len(ESPERA_503_SEGUNDOS) - 1)]
                    logger.warning(
                        f"[LLM RETRY] '{modelo}' devolveu 503 (tentativa {tentativa}/{TENTATIVAS_503}). "
                        f"Aguardando {espera}s e tentando o MESMO modelo."
                    )
                    await asyncio.sleep(espera)
        logger.error(f"[LLM RETRY] '{modelo}' seguiu em 503 apos {TENTATIVAS_503} tentativas.")
        raise ultimo

    async def _gerar_com_modelo(
        self,
        client: genai.Client,
        modelo: str,
        prompt: str,
        config: types.GenerateContentConfig,
        tool_handlers: Dict[str, Callable],
        max_tool_iterations: int,
    ) -> str:
        """Roda o ciclo completo de ferramentas para UM modelo específico."""
        contents: List[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        for iteracao in range(1, max_tool_iterations + 1):
            response = await self._chamar_com_retry(client, modelo, contents, config)

            chamadas = getattr(response, "function_calls", None)

            if not chamadas:
                texto = response.text or ""
                logger.info(
                    f"[LLM INFERENCE SUCCESS] modelo='{modelo}' respondeu "
                    f"({len(texto)} caracteres, {iteracao - 1} volta(s) de ferramenta)."
                )
                return texto

            nomes = [c.name for c in chamadas]
            logger.info(
                f"[LLM TOOL LOOP] Volta {iteracao}/{max_tool_iterations} em '{modelo}': "
                f"o modelo solicitou {len(chamadas)} função(ões): {nomes}"
            )

            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            partes: List[types.Part] = []
            for chamada in chamadas:
                resultado = await self._executar_ferramenta(
                    nome=chamada.name,
                    argumentos=dict(chamada.args or {}),
                    tool_handlers=tool_handlers,
                )
                partes.append(
                    types.Part.from_function_response(name=chamada.name, response=resultado)
                )

            contents.append(types.Content(role="user", parts=partes))

        logger.error(
            f"[LLM TOOL LOOP] Teto de {max_tool_iterations} iterações atingido em '{modelo}'."
        )
        raise RuntimeError(
            f"O modelo não concluiu a resposta em {max_tool_iterations} chamadas de ferramenta."
        )

    async def generate_response(
        self,
        system_prompt: Optional[str],
        user_message: str,
        context_chunks: Optional[List[str]] = None,
        custom_api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handlers: Optional[Dict[str, Callable]] = None,
        max_tool_iterations: int = MAX_ITERACOES_FERRAMENTAS,
    ) -> str:
        """
        Gera resposta fundamentada com contexto RAG e, opcionalmente, function calling.

        'model' agora é opcional: quando None, usa settings.DEFAULT_GEMINI_MODEL.
        O literal 'gemini-2.5-flash' que estava cravado aqui como padrão passou a
        devolver 404 em projetos novos do Google Cloud, derrubando qualquer
        chamada que não informasse o modelo explicitamente.

        Se o modelo escolhido estiver indisponível para o projeto, a cadeia de
        reserva é tentada em ordem. Cada tentativa recomeça com histórico limpo:
        reaproveitar as chamadas de ferramenta de um modelo em outro misturaria
        estados de conversas diferentes.
        """
        client = self._get_client(custom_api_key)
        tool_handlers = tool_handlers or {}

        augmented_prompt = ""
        if context_chunks:
            joined_context = "\n---\n".join(context_chunks)
            augmented_prompt += (
                "### CONTEXTO DA BASE DE CONHECIMENTO (RAG):\n"
                f"{joined_context}\n\n"
                "### INSTRUÇÃO:\n"
                "Responda à mensagem do usuário utilizando estritamente os fatos fornecidos no contexto acima. "
                "Caso a informação não esteja presente, responda educadamente que não possui a informação no momento.\n\n"
            )
        augmented_prompt += f"### MENSAGEM DO USUÁRIO:\n{user_message}"

        config_kwargs: Dict[str, Any] = {
            "system_instruction": system_prompt if system_prompt else None,
            "temperature": temperature,
        }
        if tools:
            config_kwargs["tools"] = [types.Tool(function_declarations=tools)]
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True
            )
        config = types.GenerateContentConfig(**config_kwargs)

        cadeia = self._cadeia_de_modelos(model)
        logger.info(
            f"[LLM INFERENCE] Contextos RAG: {len(context_chunks or [])}, Temp: {temperature}, "
            f"Ferramentas: {len(tools or [])}, cadeia de modelos: {cadeia}"
        )

        indisponiveis: List[str] = []
        for posicao, modelo in enumerate(cadeia, start=1):
            try:
                texto = await self._gerar_com_modelo(
                    client=client,
                    modelo=modelo,
                    prompt=augmented_prompt,
                    config=config,
                    tool_handlers=tool_handlers,
                    max_tool_iterations=max_tool_iterations,
                )
                if posicao > 1:
                    logger.warning(
                        f"[LLM FALLBACK] Modelo efetivo='{modelo}' (posição {posicao} da cadeia). "
                        f"Indisponíveis: {indisponiveis}. "
                        f"Considere corrigir ai_configs.model para evitar a tentativa perdida."
                    )
                return texto

            except ModeloIndisponivel:
                raise  # não deveria chegar aqui; mantido por clareza
            except RuntimeError:
                raise
            except Exception as exc:
                mensagem = str(exc)
                avanca, motivo = _deve_tentar_proximo(mensagem)
                if avanca:
                    indisponiveis.append(f"{modelo} ({motivo})")
                    logger.warning(
                        f"[LLM FALLBACK] Modelo '{modelo}' fora de serviço: {motivo}. "
                        f"Tentando o próximo da cadeia."
                    )
                    continue
                # Erro que NAO e de disponibilidade (payload invalido, chave
                # errada, bug nosso) continua abortando na hora: trocar de
                # modelo nao conserta, so gasta cota repetindo a mesma falha.
                logger.error(f"[LLM INFERENCE FAILED] modelo='{modelo}': {mensagem}")
                raise RuntimeError(f"Erro no processamento do modelo LLM: {mensagem}")

        logger.error(
            f"[LLM INFERENCE FAILED] A cadeia INTEIRA falhou: {indisponiveis}"
        )
        raise TodosModelosIndisponiveis(
            f"Nenhum modelo da cadeia respondeu. Tentados: {indisponiveis}"
        )

    async def generate_embedding(
        self,
        text_chunk: str,
        custom_api_key: Optional[str] = None,
        model: str = "gemini-embedding-001",
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[float]:
        """
        Gera o vetor de embedding denso (768 dimensões) para indexação ou busca semântica.

        Modelo: 'gemini-embedding-001'. O antecessor 'text-embedding-004' foi
        descontinuado e passou a responder 404 NOT_FOUND.

        task_type e assimetrico de proposito, e melhora a recuperacao:
          - RETRIEVAL_DOCUMENT -> ao indexar o documento
          - RETRIEVAL_QUERY    -> ao consultar
        A dimensao e fixada em settings.VECTOR_DIMENSION (768) para casar com a
        coluna VECTOR(768) de rag_chunks.
        """
        if not text_chunk or not text_chunk.strip():
            logger.warning("[LLM EMBEDDING] Texto vazio fornecido para embedding.")
            return [0.0] * settings.VECTOR_DIMENSION

        client = self._get_client(custom_api_key)

        try:
            from google.genai import types as _genai_types
            response = await client.aio.models.embed_content(
                model=model,
                contents=text_chunk,
                config=_genai_types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=settings.VECTOR_DIMENSION,
                ),
            )
            if hasattr(response, "embeddings") and response.embeddings:
                return list(response.embeddings[0].values)
            elif hasattr(response, "embedding") and response.embedding:
                return list(response.embedding.values)
            else:
                logger.error(f"[LLM EMBEDDING] Formato inesperado na resposta de embeddings: {response}")
                raise ValueError("Resposta de embedding vazia ou inválida.")
        except Exception as e:
            logger.error(f"[LLM EMBEDDING FAILED] Erro ao gerar vetor para o texto: {str(e)}")
            raise RuntimeError(f"Falha na geração de embeddings vetoriais: {str(e)}")


llm_service = LLMService()
