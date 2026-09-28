#!/usr/bin/env python3
"""Corrige a montagem do roteador RAG em main.py.

Um commit anterior (1de7c80) reaproveitou por engano as variaveis do RAG
para registrar o meta.py, apagando a montagem real do RAG. Efeito: TODA a
aba 10 Base de Conhecimento (upload, listagem, exclusao de documentos)
ficou fora do ar -- GET /v1/rag/documents/{tenant} devolvia 404 para
qualquer tenant.

O fix toca em UMA linha de comportamento real (a segunda montagem, que
troca de 'meta duplicado e sem uso' para 'rag.py com o prefixo correto').
A primeira montagem (que hoje e a UNICA coisa mantendo o webhook do Meta
vivo, via seu proprio prefix="/v1/meta" interno) NAO muda de alvo --
so os nomes de variavel ficam honestos, sem alterar comportamento.
Idempotente."""
import shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/main.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
orig = txt

alvo1_import = "from app.routes import rag\nfrom app.routes import meta as _rag_routes\n"
novo1_import = "from app.routes import meta as _meta_routes\n"
alvo1_include = "app.include_router(_rag_routes.router)\n"
novo1_include = "app.include_router(_meta_routes.router)\n"

alvo2_import = "from app.routes import rag\nfrom app.routes import meta as rag_module\n"
novo2_import = "from app.routes import rag\n"
alvo2_include = 'app.include_router(rag_module.router, prefix="/v1/rag")\n'
novo2_include = 'app.include_router(rag.router, prefix="/v1/rag")\n'

ja_feito = novo1_import in txt and novo2_include in txt
if ja_feito:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

faltando = []
for nome, alvo in [("bloco1_import", alvo1_import), ("bloco1_include", alvo1_include),
                    ("bloco2_import", alvo2_import), ("bloco2_include", alvo2_include)]:
    if alvo not in txt:
        faltando.append(nome)
if faltando:
    sys.exit(f"ERRO: ancoras nao encontradas: {', '.join(faltando)}")

txt = txt.replace(alvo1_import, novo1_import, 1)
txt = txt.replace(alvo1_include, novo1_include, 1)
txt = txt.replace(alvo2_import, novo2_import, 1)
txt = txt.replace(alvo2_include, novo2_include, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"main.py.{CARIMBO}")
ALVO.write_text(txt, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/main.py.{CARIMBO}")
print("Sintaxe valida.")
