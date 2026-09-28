#!/usr/bin/env python3
"""admin.html: uploadDocGlobal() mostra sucesso falso sem chamar o backend.
Casa a funcao pela ESTRUTURA (abre em 'function uploadDocGlobal() {' e fecha
na '}' da MESMA indentacao), nao pelo texto exato de dentro -- assim nao
depende de eu saber a mensagem lital, que so vi truncada antes. Idempotente."""
import re, shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/admin.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "ainda nao esta disponivel" in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

m = re.search(r'([ \t]*)function uploadDocGlobal\(\) \{\n', txt)
if not m:
    sys.exit("ERRO: 'function uploadDocGlobal() {' nao encontrada.")
indent = m.group(1)
fim = re.search(r'\n' + re.escape(indent) + r'\}\n', txt[m.end():])
if not fim:
    sys.exit("ERRO: fechamento da funcao (na mesma indentacao) nao encontrado.")

bloco_todo = txt[m.start():m.end() + fim.end()]
novo_bloco = (
    indent + "function uploadDocGlobal() {\n"
    + indent + "    const st = document.getElementById('status-upload-global');\n"
    + indent + "    st.innerHTML = '<span style=\"color:#b45309; font-weight:600;\">Upload global ainda nao esta disponivel nesta versao. Use a aba Base de Conhecimento de cada tenant.</span>';\n"
    + indent + "}\n"
)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"admin.html.{CARIMBO}")
ALVO.write_text(txt.replace(bloco_todo, novo_bloco, 1), encoding="utf-8")
print(f"Aplicado. Backup em _bak/admin.html.{CARIMBO}")
