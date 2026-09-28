#!/usr/bin/env python3
"""Corrige os dois pytest.ini, que tem os valores de testpaths TROCADOS
entre si (cada um aponta para uma pasta que so existe relativa ao OUTRO
arquivo). Tambem garante pytest+pytest-asyncio no requirements.txt.
Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
INI_RAIZ = RAIZ / "backend/pytest.ini"
INI_APP = RAIZ / "backend/app/pytest.ini"
REQS = RAIZ / "backend/requirements.txt"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)

def trocar(caminho, de, para):
    txt = caminho.read_text(encoding="utf-8")
    if f"testpaths = {para}" in txt:
        print(f"[{caminho.name}] ja corrigido -- nada a fazer.")
        return False
    if f"testpaths = {de}" not in txt:
        sys.exit(f"ERRO: 'testpaths = {de}' nao encontrado em {caminho}.")
    shutil.copy2(caminho, bak / f"{caminho.parent.name}_{caminho.name}.{CARIMBO}")
    caminho.write_text(txt.replace(f"testpaths = {de}", f"testpaths = {para}", 1), encoding="utf-8")
    print(f"[{caminho.name}] testpaths: '{de}' -> '{para}'.")
    return True

trocar(INI_RAIZ, "tests", "app/tests")
trocar(INI_APP, "app/tests", "tests")

reqs = REQS.read_text(encoding="utf-8")
faltando = [p for p in ("pytest", "pytest-asyncio") if p not in reqs]
if faltando:
    shutil.copy2(REQS, bak / f"requirements.txt.{CARIMBO}")
    novo = reqs.rstrip("\n") + "\n" + "\n".join(faltando) + "\n"
    REQS.write_text(novo, encoding="utf-8")
    print("[requirements.txt] adicionado:", ", ".join(faltando))
else:
    print("[requirements.txt] ja tem pytest e pytest-asyncio -- nada a fazer.")
