#!/usr/bin/env python3
"""rag_management.html: o JS procurava getElementById('rag-documents-tbody'),
mas o <tbody> real se chama 'rag-documents-table-body' -- 'if (!corpo) return;'
sempre disparava, entao a lista de documentos JAMAIS era desenhada, mesmo com
uploads reais indexados com sucesso no banco (confirmado: 3 documentos ja
indexados, invisiveis na tela desde antes desta sessao). Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/rag_management.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
ALVO_STR = 'getElementById("rag-documents-tbody")'
NOVO_STR = 'getElementById("rag-documents-table-body")'

if NOVO_STR in txt and ALVO_STR not in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)
if ALVO_STR not in txt:
    sys.exit("ERRO: string 'rag-documents-tbody' nao encontrada no arquivo.")

novo_txt = txt.replace(ALVO_STR, NOVO_STR)
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag_management.html.{CARIMBO}")
ALVO.write_text(novo_txt, encoding="utf-8")
print(f"Aplicado. Backup em _bak/rag_management.html.{CARIMBO}")
