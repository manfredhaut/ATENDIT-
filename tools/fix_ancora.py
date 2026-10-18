#!/usr/bin/env python3
"""Corrige a linha ANCORA_BOTAO dentro do proprio script de patch (nao no
calendar_config.html) -- faltavam propriedades de estilo (border,
border-radius, font-size, font-weight, cursor) que a reconstrucao anterior,
feita de uma saida truncada por 'cut -c1-160', nao incluia."""
import sys
from pathlib import Path

ALVO = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit/tools/patch_cruzamento_waitlist_frontend.py")
txt = ALVO.read_text(encoding="utf-8")

ANTIGO = '''ANCORA_BOTAO = "+ '    <button type=\\"button\\" onclick=\\"calRemoverWaitlist(\\\\'' + item.id + '\\\\')\\" style=\\"padding:6px 12px; background:#FEE2E2; color:#991B1B;\\">Remover</button>'"'''
NOVO = '''ANCORA_BOTAO = "+ '    <button type=\\"button\\" onclick=\\"calRemoverWaitlist(\\\\'' + item.id + '\\\\')\\" style=\\"padding:6px 12px; background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; border-radius:6px; font-size:0.8rem; font-weight:600; cursor:pointer;\\">Remover</button>'"'''

if NOVO in txt:
    print("Ja corrigido -- nada a fazer.")
    sys.exit(0)
if ANTIGO not in txt:
    sys.exit("ERRO: linha ANCORA_BOTAO (versao com o bug) nao encontrada -- talvez ja tenha sido editada manualmente.")

txt = txt.replace(ANTIGO, NOVO, 1)
ALVO.write_text(txt, encoding="utf-8")
print("Corrigido.")
