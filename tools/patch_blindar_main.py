#!/usr/bin/env python3
"""Blinda o loader de views (/dashboards/{view_name}) contra UnicodeDecodeError.
Um byte invalido em UM arquivo .html nao pode mais derrubar a tela com 500.
So altera main.py. Idempotente."""
import re, shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/main.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "bytes invalidos" in txt:
    print("Ja blindado -- nada a fazer.")
    sys.exit(0)

padrao = re.compile(
    r'[ \t]*with open\(target_file, "r", encoding="utf-8"\) as f:\n'
    r'[ \t]*from fastapi\.responses import HTMLResponse\n'
    r'[ \t]*return HTMLResponse\(content=f\.read\(\), media_type="text/html"\)\n'
)
m = padrao.search(txt)
if not m:
    sys.exit("ERRO: bloco 'with open(target_file...)' nao encontrado no formato esperado.")

novo_bloco = (
    '    from fastapi.responses import HTMLResponse\n'
    '    import logging as _lg\n'
    '    bruto = target_file.read_bytes()\n'
    '    try:\n'
    '        conteudo = bruto.decode("utf-8")\n'
    '    except UnicodeDecodeError as _e:\n'
    '        # Um unico byte invalido nao pode derrubar a tela inteira com 500.\n'
    '        conteudo = bruto.decode("utf-8", errors="replace")\n'
    '        _lg.getLogger("uvicorn.error").warning(\n'
    '            "View %s com bytes invalidos (%s na posicao %s); servida com substituicao.",\n'
    '            safe_name, _e.reason, _e.start)\n'
    '    return HTMLResponse(content=conteudo, media_type="text/html")\n'
)
novo = txt[:m.start()] + novo_bloco + txt[m.end():]

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"main.py.{CARIMBO}")
ALVO.write_text(novo, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/main.py.{CARIMBO}")
print("Sintaxe valida.")
