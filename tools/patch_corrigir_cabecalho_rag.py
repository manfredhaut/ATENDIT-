#!/usr/bin/env python3
"""rag_management.html: o <thead> tinha 4 colunas (ID/Nome/Data/Acao) mas o
JS sempre desenhou 5 tds por linha (Nome/Tamanho/Status/Data/Acao) -- as
duas partes foram editadas em momentos diferentes e nunca mais comparadas.
Resultado: cabecalho nao bate com o conteudo (ex.: coluna 'ID do Documento'
mostra o nome do arquivo).

Abordagem robusta a indentacao: nao hardcoda espacos/tabs -- captura a
indentacao e o estilo REAL do <th> de 'Nome do Arquivo' via regex e
reaproveita para as duas colunas novas, e remove a coluna 'ID do
Documento (pgvector)' inteira (nao ha ID nenhum sendo mostrado ali).
Idempotente."""
import re, shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/rag_management.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")

if "Tamanho</th>" in txt and "Status</th>" in txt and "ID do Documento (pgvector)" not in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

padrao_id = re.compile(r'[ \t]*<th[^>]*>ID do Documento \(pgvector\)</th>\r?\n')
if "ID do Documento (pgvector)" in txt and not padrao_id.search(txt):
    sys.exit("ERRO: 'ID do Documento (pgvector)' existe no texto mas o padrao regex nao casou a linha inteira.")
txt2 = padrao_id.sub('', txt, count=1)

padrao_nome = re.compile(r'([ \t]*)(<th[^>]*>)Nome do Arquivo(</th>\r?\n)')
m = padrao_nome.search(txt2)
if not m:
    sys.exit("ERRO: coluna 'Nome do Arquivo' nao encontrada no cabecalho.")
indent, abre_tag, fecha = m.group(1), m.group(2), m.group(3)
insercao = (indent + abre_tag + "Tamanho" + fecha +
            indent + abre_tag + "Status" + fecha)
txt3 = txt2[:m.end()] + insercao + txt2[m.end():]

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag_management.html.{CARIMBO}")
ALVO.write_text(txt3, encoding="utf-8")
print(f"Aplicado. Backup em _bak/rag_management.html.{CARIMBO}")
