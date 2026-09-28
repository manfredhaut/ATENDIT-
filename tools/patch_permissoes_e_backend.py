#!/usr/bin/env python3
"""Lote backend (so .py):
1) autorizacao.py: tira 'faturamento' das listas de Gestor e Leitura (a
   descricao ja dizia 'sem financeiro' / so metricas -- a LISTA estava
   errada). Acrescenta a nova view do aviso Fase 3 na lista do Gestor.
2) conta_tenant.py: menu item 6 passa a apontar para o aviso honesto de
   Fase 3 em vez de abrir 'equipe' direto (que agora vive como sub-aba
   dentro de Empresa & Conta).
3) conta_tenant.py: /api/templates/aplicar passa a gravar o prompt do
   modelo tambem em AIConfig.system_instruction -- hoje so gravava em
   Tenant.meta_data, que nada le; o webhook so consulta AIConfig.
Idempotente; cada uma das 3 mudancas e checada e aplicada de forma
independente."""
import re, shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
AUTZ = RAIZ / "backend/app/core/autorizacao.py"
CONTA = RAIZ / "backend/app/routes/conta_tenant.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)

mudou_autz = mudou_conta = False

# --- 1) autorizacao.py -------------------------------------------------
txt = AUTZ.read_text(encoding="utf-8")
orig = txt

alvo_gestor = (
    '        "views": [\n'
    '            "faturamento", "configuracao_guiada", "modelos_segmento", "empresa_cadastro",\n'
)
novo_gestor = (
    '        "views": [\n'
    '            "configuracao_guiada", "modelos_segmento", "empresa_cadastro",\n'
    '            "aviso_fase3_profissionais",\n'
)
if alvo_gestor in txt:
    txt = txt.replace(alvo_gestor, novo_gestor, 1)
    mudou_autz = True

if txt.count('"views": ["faturamento", "intel_operacional"]') == 2:
    partes = txt.rsplit('"views": ["faturamento", "intel_operacional"]', 1)
    txt = partes[0] + '"views": ["intel_operacional"]' + partes[1]
    mudou_autz = True

if txt != orig:
    shutil.copy2(AUTZ, bak / f"autorizacao.py.{CARIMBO}")
    AUTZ.write_text(txt, encoding="utf-8")
    py_compile.compile(str(AUTZ), doraise=True)
    print("[autorizacao.py] aplicado.")
else:
    print("[autorizacao.py] ja aplicado ou ancoras nao encontradas -- nada mudou.")

# --- 2) conta_tenant.py: menu item 6 -----------------------------------
txt2 = CONTA.read_text(encoding="utf-8")
orig2 = txt2

alvo_menu = '<a class="item" data-v="equipe"><span class="m-icon">👥</span> 6. Profissionais & Escalas</a>'
novo_menu = '<a class="item" data-v="aviso_fase3_profissionais"><span class="m-icon">👥</span> 6. Profissionais & Escalas</a>'
if alvo_menu in txt2:
    txt2 = txt2.replace(alvo_menu, novo_menu, 1)
    mudou_conta = True
elif novo_menu in txt2:
    pass
else:
    print("[conta_tenant.py] AVISO: ancora do menu item 6 nao encontrada.")

alvo_backend = (
    '        tenant.meta_data = meta\n'
    '        await session.commit()\n'
    '\n'
    '    return JSONResponse(content={\n'
    '        "ok": True,\n'
    '        "mensagem": f"Modelo \'{template[\'nome\']}\' aplicado com sucesso!",\n'
    '        "template": template\n'
    '    })\n'
)
novo_backend = (
    '        tenant.meta_data = meta\n'
    '\n'
    '        # Alem de guardar no tenant (para o funil da Fase 3), aplica o prompt\n'
    '        # do modelo como persona ATIVA da IA agora -- e o unico jeito de o\n'
    '        # webhook (routes/webhook.py) realmente usar o modelo escolhido, ja\n'
    '        # que ele le AIConfig.system_instruction, nao Tenant.meta_data.\n'
    '        from app.models.tenant import AIConfig\n'
    '        res_ai = await session.execute(select(AIConfig).where(AIConfig.tenant_id == t_uuid))\n'
    '        ai_cfg = res_ai.scalar_one_or_none()\n'
    '        if ai_cfg is None:\n'
    '            ai_cfg = AIConfig(tenant_id=t_uuid)\n'
    '            session.add(ai_cfg)\n'
    '        ai_cfg.system_instruction = template["prompt_ia"]\n'
    '\n'
    '        await session.commit()\n'
    '\n'
    '    return JSONResponse(content={\n'
    '        "ok": True,\n'
    '        "mensagem": (\n'
    '            f"Modelo \'{template[\'nome\']}\' aplicado! A persona da IA foi atualizada "\n'
    '            "com as instrucoes deste modelo (substitui o texto que estava em "\n'
    '            "Assistente IA \u203a Persona e Tom)."\n'
    '        ),\n'
    '        "template": template\n'
    '    })\n'
)
if alvo_backend in txt2:
    txt2 = txt2.replace(alvo_backend, novo_backend, 1)
    mudou_conta = True
elif "res_ai = await session.execute(select(AIConfig)" in txt2:
    pass
else:
    print("[conta_tenant.py] AVISO: bloco /api/templates/aplicar nao encontrado.")

if txt2 != orig2:
    shutil.copy2(CONTA, bak / f"conta_tenant.py.{CARIMBO}")
    CONTA.write_text(txt2, encoding="utf-8")
    py_compile.compile(str(CONTA), doraise=True)
    print("[conta_tenant.py] aplicado.")
else:
    print("[conta_tenant.py] nada a fazer.")

print("\nOK: arquivos alterados compilam.")
