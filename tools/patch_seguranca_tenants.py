#!/usr/bin/env python3
"""Fecha /tenants e /api/v1/presenthia sem sessao de admin. So altera panel_auth.py. Idempotente."""
import re, shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/core/panel_auth.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if '"/tenants",' in txt and '"/api/v1/presenthia",' in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

padrao = re.compile(
    r'(CAMINHOS_SOMENTE_ADMIN = \(\n'
    r'(?:[ \t]*"[^"]*",\n)*)'
    r'([ \t]*\))'
)
m = padrao.search(txt)
if not m:
    sys.exit("ERRO: bloco CAMINHOS_SOMENTE_ADMIN nao encontrado no formato esperado.")

insercao = (
    '    # 2026-09-28: /tenants (routes/tenants.py) lista TODOS os tenants\n'
    '    # (nome, e-mail, trial) sem sessao alguma -- medido publicamente: HTTP 200\n'
    '    # sem cookie. O prefixo real do roteador e "/tenants", nao "/v1/tenants";\n'
    '    # a entrada antiga da lista protegia um caminho que o codigo nao usa mais.\n'
    '    "/tenants",\n'
    '    # Console administrativo Presenthia: so tem Depends(flag_on(...)), que\n'
    '    # verifica se a FUNCIONALIDADE esta ligada, nao quem esta pedindo. Sem\n'
    '    # esta linha, ligar a flag globalmente abre o console para qualquer um.\n'
    '    "/api/v1/presenthia",\n'
)
novo = txt[:m.start(2)] + insercao + txt[m.start(2):]

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"panel_auth.py.{CARIMBO}")
ALVO.write_text(novo, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/panel_auth.py.{CARIMBO}")
print("Sintaxe valida.")
