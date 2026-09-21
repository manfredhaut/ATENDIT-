import logging
import re
import uuid
from pathlib import Path
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag import RAGChunk, RAGDocument
from app.services.llm_service import llm_service

logger = logging.getLogger("atendit.rag_service")

# Fragmentacao: ~500 tokens por trecho, ~50 de sobreposicao.
# O SDK do Gemini nao expoe tokenizador local, entao a conversao usa a
# aproximacao de ~4 caracteres por token, que e razoavel para portugues.
# Os alvos ficam explicitos em TOKENS para que o dia em que houver um
# tokenizador de verdade a troca seja de uma linha.
TOKENS_POR_CHUNK = 500
TOKENS_SOBREPOSICAO = 50
CARACTERES_POR_TOKEN = 4
TAMANHO_CHUNK = TOKENS_POR_CHUNK * CARACTERES_POR_TOKEN        # 2000 caracteres
SOBREPOSICAO_CHUNK = TOKENS_SOBREPOSICAO * CARACTERES_POR_TOKEN  # 200 caracteres

MODELO_EMBEDDING = "gemini-embedding-001"
DIMENSOES_EMBEDDING = 768


class RAGService:
    """Serviço de ingestão, indexação e busca semântica vetorial com pgvector."""

    # ------------------------------------------------------------------
    # EXTRAÇÃO DE TEXTO
    # ------------------------------------------------------------------
    def extrair_texto(self, caminho: Path) -> str:
        """
        Extrai o texto bruto do documento conforme a extensão.

        Levanta ValueError para formato não suportado ou arquivo ilegível —
        quem chama converte isso em status='failed'.
        """
        extensao = caminho.suffix.lower()

        if extensao in (".txt", ".md"):
            bruto = caminho.read_bytes()
            # utf-8 e o caso normal; latin-1 nunca falha e evita que um
            # arquivo com acentuacao mal codificada derrube a ingestao.
            try:
                return bruto.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"[RAG EXTRACAO] '{caminho.name}' nao e UTF-8; usando latin-1.")
                return bruto.decode("latin-1", errors="replace")

        if extensao == ".pdf":
            from pypdf import PdfReader

            leitor = PdfReader(str(caminho))
            paginas = []
            for numero, pagina in enumerate(leitor.pages, start=1):
                try:
                    paginas.append(pagina.extract_text() or "")
                except Exception as exc:
                    # Uma pagina problematica nao invalida o documento inteiro.
                    logger.warning(f"[RAG EXTRACAO] Falha na pagina {numero} de '{caminho.name}': {exc}")
            return "\n".join(paginas)

        if extensao == ".docx":
            import docx

            documento = docx.Document(str(caminho))
            partes = [p.text for p in documento.paragraphs]
            # Tabelas carregam informacao real (precos, horarios) e sao
            # ignoradas se lermos apenas os paragrafos.
            for tabela in documento.tables:
                for linha in tabela.rows:
                    celulas = [c.text.strip() for c in linha.cells if c.text.strip()]
                    if celulas:
                        partes.append(" | ".join(celulas))
            return "\n".join(partes)

        raise ValueError(f"Extensão não suportada para extração de texto: '{extensao}'")

    # ------------------------------------------------------------------
    # FRAGMENTAÇÃO
    # ------------------------------------------------------------------
    def fragmentar(self, texto: str) -> List[str]:
        """
        Divide o texto em janelas de ~TAMANHO_CHUNK com ~SOBREPOSICAO_CHUNK
        de superposição, sem cortar palavra ao meio.

        A sobreposição existe para que uma frase que cai exatamente na fronteira
        de dois trechos continue recuperável — sem ela, a informação da emenda
        fica invisível para a busca.
        """
        texto = re.sub(r"\s+", " ", texto or "").strip()
        if not texto:
            return []
        if len(texto) <= TAMANHO_CHUNK:
            return [texto]

        pedacos: List[str] = []
        inicio = 0
        while inicio < len(texto):
            fim = min(inicio + TAMANHO_CHUNK, len(texto))

            # Recua ate o ultimo espaco para nao partir palavra.
            if fim < len(texto):
                corte = texto.rfind(" ", inicio, fim)
                if corte > inicio:
                    fim = corte

            pedaco = texto[inicio:fim].strip()
            if pedaco:
                pedacos.append(pedaco)

            if fim >= len(texto):
                break

            # max(..., inicio + 1) garante progresso: sem isso, um trecho sem
            # espacos poderia fazer o laco nunca avancar.
            inicio = max(fim - SOBREPOSICAO_CHUNK, inicio + 1)

        return pedacos

    # ------------------------------------------------------------------
    # INGESTÃO
    # ------------------------------------------------------------------
    async def indexar_documento(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        caminho: Path,
        filename: str,
        tamanho_bytes: int,
        mime_type: str = "application/octet-stream",
        custom_api_key: Optional[str] = None,
    ) -> dict:
        """
        Pipeline completo: extrai -> fragmenta -> gera embeddings -> grava.

        Contrato de falha: o documento nasce 'processing'. Se qualquer etapa
        falhar, ele termina 'failed' e NENHUM chunk fica gravado.

        Isso e garantido pela ordem: TODOS os embeddings sao gerados ANTES de
        qualquer chunk ser inserido. Gerar e inserir de forma intercalada
        deixaria trechos gravados e outros nao no momento de uma falha de
        cota — uma base meio indexada que responde com confianca sobre metade
        do documento, que e pior do que nao responder.
        """
        documento_id = uuid.uuid4()

        documento = RAGDocument(
            id=documento_id,
            tenant_id=tenant_id,
            filename=filename,
            file_path=str(caminho),
            file_size=tamanho_bytes,
            mime_type=mime_type or "application/octet-stream",
            status="processing",
            meta_data={},
        )
        db.add(documento)
        await db.commit()
        logger.info(f"[RAG INGESTAO] Documento {documento_id} criado como 'processing' (tenant {tenant_id}).")

        try:
            texto = self.extrair_texto(caminho)
            if not texto or not texto.strip():
                raise ValueError("Nenhum texto foi extraído do documento (arquivo vazio ou apenas imagem).")

            pedacos = self.fragmentar(texto)
            if not pedacos:
                raise ValueError("A fragmentação não produziu nenhum trecho utilizável.")

            logger.info(
                f"[RAG INGESTAO] '{filename}': {len(texto)} caracteres -> {len(pedacos)} trecho(s). "
                f"Gerando embeddings ({MODELO_EMBEDDING}, {DIMENSOES_EMBEDDING}d)."
            )

            vetores: List[List[float]] = []
            for indice, pedaco in enumerate(pedacos):
                vetor = await llm_service.generate_embedding(
                    text_chunk=pedaco,
                    custom_api_key=custom_api_key,
                    task_type="RETRIEVAL_DOCUMENT",
                )
                if len(vetor) != DIMENSOES_EMBEDDING:
                    raise ValueError(
                        f"Embedding do trecho {indice} veio com {len(vetor)} dimensões; "
                        f"a coluna exige {DIMENSOES_EMBEDDING}."
                    )
                vetores.append(vetor)

            # A partir daqui nao ha mais chamada externa: so escrita local.
            for indice, (pedaco, vetor) in enumerate(zip(pedacos, vetores)):
                db.add(
                    RAGChunk(
                        id=uuid.uuid4(),
                        document_id=documento_id,
                        tenant_id=tenant_id,
                        chunk_index=indice,
                        content=pedaco,
                        embedding=vetor,
                        meta_data={"modelo": MODELO_EMBEDDING, "dim": DIMENSOES_EMBEDDING},
                    )
                )

            await db.execute(
                update(RAGDocument)
                .where(RAGDocument.id == documento_id)
                .values(
                    status="indexed",
                    meta_data={
                        "chunks": len(pedacos),
                        "caracteres": len(texto),
                        "modelo_embedding": MODELO_EMBEDDING,
                        "dimensoes": DIMENSOES_EMBEDDING,
                    },
                )
            )
            await db.commit()

            logger.info(
                f"[RAG INGESTAO SUCESSO] Documento {documento_id} indexado: {len(pedacos)} trecho(s) gravados."
            )
            return {
                "document_id": str(documento_id),
                "status": "indexed",
                "chunks_indexados": len(pedacos),
                "caracteres_extraidos": len(texto),
            }

        except Exception as exc:
            logger.error(f"[RAG INGESTAO FALHOU] Documento {documento_id}: {exc}")
            # Descarta os chunks ainda nao confirmados desta transacao...
            await db.rollback()
            # ...e remove qualquer chunk que porventura tenha sido confirmado,
            # para que 'failed' nunca conviva com trecho pendurado.
            await db.execute(delete(RAGChunk).where(RAGChunk.document_id == documento_id))
            await db.execute(
                update(RAGDocument)
                .where(RAGDocument.id == documento_id)
                .values(status="failed", meta_data={"erro": str(exc)[:500]})
            )
            await db.commit()
            raise

    # ------------------------------------------------------------------
    # BUSCA
    # ------------------------------------------------------------------
    async def search_relevant_context(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query_text: str,
        custom_api_key: Optional[str] = None,
        limit: int = 3,
    ) -> List[str]:
        """
        Busca os fragmentos de texto mais similares à pergunta do usuário utilizando
        distância de cosseno (<=>) na tabela rag_chunks isolada por tenant.
        """
        if not query_text or not query_text.strip():
            logger.warning("[RAG SEARCH] Consulta semântica vazia recebida.")
            return []

        logger.info(
            f"[RAG SEARCH] Iniciando busca vetorial para Tenant {tenant_id} "
            f"(Query: '{query_text[:60]}...', Top-K: {limit})"
        )

        try:
            # 1. Gera o vetor de embedding da consulta via LLMService (768 dimensões)
            # RETRIEVAL_QUERY: lado da CONSULTA do par assimetrico de embeddings
            query_vector = await llm_service.generate_embedding(
                text_chunk=query_text,
                custom_api_key=custom_api_key,
                task_type="RETRIEVAL_QUERY",
            )

            # 2. Executa a busca vetorial por distância de cosseno (<=>) no pgvector
            # RAGChunk.embedding.cosine_distance gera 'rag_chunks.embedding <=> query_vector'
            distance_expr = RAGChunk.embedding.cosine_distance(query_vector)

            stmt = (
                select(RAGChunk.content, distance_expr.label("distance"))
                .where(
                    RAGChunk.tenant_id == tenant_id,
                    RAGChunk.embedding.isnot(None)
                )
                .order_by(distance_expr.asc())
                .limit(limit)
            )

            result = await db.execute(stmt)
            rows = result.all()

            context_chunks: List[str] = []
            for row in rows:
                content, distance = row
                logger.info(f"[RAG MATCH] Chunk recuperado (Distância Cosseno: {distance:.4f})")
                context_chunks.append(content)

            logger.info(
                f"[RAG SEARCH SUCCESS] {len(context_chunks)} chunks recuperados para o Tenant {tenant_id}."
            )
            return context_chunks

        except Exception as e:
            logger.error(
                f"[RAG SEARCH ERROR] Falha na consulta vetorial para o Tenant {tenant_id}: {str(e)}"
            )
            # Em caso de falha de RAG, não interrompe o fluxo; permite que a IA responda sem contexto
            return []


rag_service = RAGService()
