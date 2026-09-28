#!/usr/bin/env python3
"""Corrige a fragilidade de autorizacao nas 3 rotas de /v1/whatsapp/chats*.

Padrao antigo: se nao ha slug nem no parametro/corpo nem na sessao, o
'if slug:' nunca executa e a checagem de autorizacao e PULADA -- a rota
segue em frente com slug=None. Hoje isso nao vaza dado porque a consulta
SQL usa 'WHERE slug = :slug' e NULL nunca bate com nada, mas essa
protecao e um acidente do SQL, nao uma decisao do codigo -- qualquer
refatoracao futura pode desfaze-la sem ninguem perceber.

Fix: ausencia de slug apos as duas tentativas (parametro/corpo E sessao)
vira uma recusa EXPLICITA (401), no lugar de seguir em frente. Idempotente."""
import re, shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/main.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")

ALVO_BLOCO = (
    "    if not slug:\n"
    "        slug = await _autz.slug_da_sessao(request)\n"
    "    if slug:\n"
    "        await _autz.exigir_acesso_ao_tenant(request, slug=slug)\n"
)
NOVO_BLOCO = (
    "    if not slug:\n"
    "        slug = await _autz.slug_da_sessao(request)\n"
    "    if not slug:\n"
    "        raise HTTPException(status_code=401, detail=\"Sessão ausente ou expirada. Faça login.\")\n"
    "    await _autz.exigir_acesso_ao_tenant(request, slug=slug)\n"
)

n_antes = txt.count(ALVO_BLOCO)
n_ja_feito = txt.count(NOVO_BLOCO)

if n_antes == 0 and n_ja_feito >= 3:
    print(f"Ja aplicado nas {n_ja_feito} ocorrencias -- nada a fazer.")
    sys.exit(0)
if n_antes != 3:
    sys.exit(f"ERRO: esperava exatamente 3 ocorrencias do bloco antigo, encontrei {n_antes}. "
              f"Abortando sem alterar nada -- confirme o texto exato antes de tentar de novo.")

novo_txt = txt.replace(ALVO_BLOCO, NOVO_BLOCO)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"main.py.{CARIMBO}")
ALVO.write_text(novo_txt, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado nas 3 ocorrencias. Backup em _bak/main.py.{CARIMBO}")
print("Sintaxe valida.")
