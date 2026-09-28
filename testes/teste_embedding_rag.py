"""
TESTE 1 - Embedding gemini-embedding-001 + busca vetorial pgvector.

Prova, nesta ordem:
  1. gemini-embedding-001 responde 200 (o antecessor text-embedding-004 devolvia 404)
  2. o vetor sai com exatamente 768 dimensoes, casando com VECTOR(768)
  3. a ingestao usa RETRIEVAL_DOCUMENT e a consulta usa RETRIEVAL_QUERY
  4. a busca por cosseno recupera o trecho certo
  5. o filtro por tenant continua isolando

FIXTURE AUTOLIMPANTE: cria 1 documento e 3 chunks, e apaga tudo no finally.
O estado anterior era 0 documentos / 0 chunks, entao apagar restaura exatamente.
Nao sobrescreve nenhuma coluna de producao - so insere e remove linhas proprias.
"""

import asyncio
import sys
import uuid

from sqlalchemy import delete, text

from app.core.database import AsyncSessionLocal
from app.models.rag import RAGChunk, RAGDocument
from app.services.llm_service import llm_service
from app.services.rag_service import rag_service

FALHAS = []


def checar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    if condicao:
        print(f"  [OK]     {descricao}")
    else:
        print(f"  [FALHOU] {descricao} {detalhe}")
        FALHAS.append(descricao)


DOCUMENTOS = [
    "O horario de funcionamento da secretaria do clube e de segunda a sexta, das 8h as 18h.",
    "A taxa de manutencao mensal do socio titular e de R$ 180,00, com vencimento todo dia 10.",
    "A piscina aquecida passa por manutencao preventiva na primeira segunda-feira de cada mes.",
]

CONSULTA = "Qual o valor da mensalidade do socio?"
INDICE_ESPERADO = 1  # o trecho da taxa de manutencao


async def principal() -> None:
    doc_id = uuid.uuid4()
    tenant_id = None

    async with AsyncSessionLocal() as sessao:
        tenant_id = (await sessao.execute(text("SELECT id FROM tenants LIMIT 1"))).scalar_one()
        # Mesma precedencia do webhook: a chave do inquilino vem antes da do
        # ambiente. Hoje a do ambiente esta truncada (31 chars) e o Google a
        # rejeita, entao usar a do inquilino tambem torna o teste fiel ao
        # caminho real de producao.
        chave = (
            await sessao.execute(text("SELECT api_key FROM ai_configs WHERE tenant_id = :t"),
                                 {"t": tenant_id})
        ).scalar_one_or_none()
        print(f"\nTenant de teste: {tenant_id}")
        print(f"Chave usada: do inquilino (len={len(chave) if chave else 0})")

        antes_docs = (await sessao.execute(text("SELECT count(*) FROM rag_documents"))).scalar_one()
        antes_chunks = (await sessao.execute(text("SELECT count(*) FROM rag_chunks"))).scalar_one()
        print(f"Estado ANTES: {antes_docs} documentos / {antes_chunks} chunks")

    try:
        # ---------- ETAPA 1: embeddings de INGESTAO ----------
        print("\n[ETAPA 1] Gerando embeddings de ingestao (RETRIEVAL_DOCUMENT)")
        vetores = []
        for i, texto in enumerate(DOCUMENTOS):
            vetor = await llm_service.generate_embedding(
                text_chunk=texto,
                task_type="RETRIEVAL_DOCUMENT",
                custom_api_key=chave,
            )
            vetores.append(vetor)
            print(f"  chunk {i}: {len(vetor)} dimensoes")

        checar(len(vetores) == 3, "3 embeddings gerados sem erro 404")
        checar(
            all(len(v) == 768 for v in vetores),
            "todos os vetores tem 768 dimensoes",
            f"(obtido: {[len(v) for v in vetores]})",
        )
        checar(
            any(abs(x) > 1e-9 for x in vetores[0]),
            "vetor nao e um zero-vector (a API devolveu conteudo real)",
        )

        # ---------- ETAPA 2: grava a fixture ----------
        print("\n[ETAPA 2] Gravando fixture no Postgres")
        async with AsyncSessionLocal() as sessao:
            sessao.add(
                RAGDocument(
                    id=doc_id,
                    tenant_id=tenant_id,
                    filename="__FIXTURE_TESTE_EMBEDDING__.txt",
                    file_path="/tmp/__fixture_teste_embedding__.txt",
                    file_size=sum(len(d) for d in DOCUMENTOS),
                    mime_type="text/plain",
                    status="indexed",
                    meta_data={"fixture": True},
                )
            )
            for i, (texto, vetor) in enumerate(zip(DOCUMENTOS, vetores)):
                sessao.add(
                    RAGChunk(
                        id=uuid.uuid4(),
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        chunk_index=i,
                        content=texto,
                        embedding=vetor,
                        meta_data={"fixture": True, "modelo": "gemini-embedding-001", "dim": 768},
                    )
                )
            await sessao.commit()
        print(f"  documento {doc_id} + 3 chunks gravados")

        # ---------- ETAPA 3: busca vetorial ----------
        print(f"\n[ETAPA 3] Busca vetorial (RETRIEVAL_QUERY) para: '{CONSULTA}'")
        async with AsyncSessionLocal() as sessao:
            recuperados = await rag_service.search_relevant_context(
                db=sessao,
                tenant_id=tenant_id,
                query_text=CONSULTA,
                custom_api_key=chave,
                limit=3,
            )

        for i, trecho in enumerate(recuperados):
            print(f"  #{i + 1}: {trecho[:70]}...")

        checar(len(recuperados) > 0, "a busca vetorial retornou resultado (sem erro 404)")
        checar(
            bool(recuperados) and recuperados[0] == DOCUMENTOS[INDICE_ESPERADO],
            "o trecho MAIS relevante e o correto (taxa de manutencao)",
            f"(veio: '{recuperados[0][:50] if recuperados else 'nada'}...')",
        )

        # ---------- ETAPA 4: isolamento por tenant ----------
        print("\n[ETAPA 4] Isolamento: buscando com tenant_id inexistente")
        async with AsyncSessionLocal() as sessao:
            vazio = await rag_service.search_relevant_context(
                db=sessao,
                tenant_id=uuid.uuid4(),
                query_text=CONSULTA,
                custom_api_key=chave,
                limit=3,
            )
        checar(vazio == [], "tenant alheio nao recupera nada", f"(veio {len(vazio)} trechos)")

    finally:
        # ---------- LIMPEZA ----------
        print("\n[LIMPEZA] Removendo a fixture")
        async with AsyncSessionLocal() as sessao:
            await sessao.execute(delete(RAGChunk).where(RAGChunk.document_id == doc_id))
            await sessao.execute(delete(RAGDocument).where(RAGDocument.id == doc_id))
            await sessao.commit()

            depois_docs = (await sessao.execute(text("SELECT count(*) FROM rag_documents"))).scalar_one()
            depois_chunks = (await sessao.execute(text("SELECT count(*) FROM rag_chunks"))).scalar_one()
            print(f"Estado DEPOIS: {depois_docs} documentos / {depois_chunks} chunks")
            checar(
                depois_docs == antes_docs and depois_chunks == antes_chunks,
                "estado do banco restaurado ao original",
            )


if __name__ == "__main__":
    asyncio.run(principal())
    print("\n" + "=" * 60)
    if FALHAS:
        print(f"TESTE 1: FALHOU ({len(FALHAS)} verificacao(oes))")
        for f in FALHAS:
            print(f"  - {f}")
        sys.exit(1)
    print("TESTE 1: PASSOU")
    sys.exit(0)
