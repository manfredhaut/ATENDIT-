#!/usr/bin/env python3
"""F1.3 -> F1.2: card de onboarding no Inicio + blindagem do loader de views. Idempotente."""
import re, shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
CONTA = RAIZ / "backend/app/routes/conta_tenant.py"
MAIN = RAIZ / "backend/app/main.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
MARCA = "onb-card"

CARD = '''    <!-- ATALHO F1.3 - CONFIGURACAO GUIADA (ONBOARDING) -->
    <div id="onb-card" style="background:#ffffff; padding:20px 22px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc); border-left:4px solid var(--p-magenta, #D0006F);">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;">
        <div style="flex:1; min-width:240px;">
          <div style="font-weight:700; font-size:0.95rem; color:var(--p-texto, #231726); margin-bottom:4px;">🚀 Configuração Guiada (Onboarding)</div>
          <p id="onb-texto" style="font-size:0.82rem; color:var(--p-texto-suave, #5e5563); margin:0;">Carregando progresso...</p>
        </div>
        <div style="text-align:right;">
          <div id="onb-pct" style="font-size:1.6rem; font-weight:800; color:var(--p-magenta, #D0006F); line-height:1;">--</div>
          <div style="font-size:0.7rem; font-weight:700; color:#64748b; text-transform:uppercase;">concluído</div>
        </div>
      </div>
      <div style="margin-top:14px; height:8px; background:#f1f5f9; border-radius:999px; overflow:hidden;">
        <div id="onb-barra" style="height:100%; width:0%; background:linear-gradient(90deg, #D0006F, #3ccbc5); border-radius:999px; transition:width .4s ease;"></div>
      </div>
      <button onclick="carregarView('configuracao_guiada', null)" style="margin-top:14px; padding:10px 18px; background:var(--p-magenta, #D0006F); color:#ffffff; border:none; border-radius:10px; font-weight:700; font-size:0.85rem; cursor:pointer;">Abrir Configuração Guiada →</button>
    </div>

'''

FUNCAO_JS = '''async function atualizarOnboardingInicio() {{
    try {{
      const resp = await fetch('/api/tenant/onboarding-status');
      if (!resp.ok) return;
      const d = await resp.json();
      if (!d.ok) return;
      const barra = document.getElementById('onb-barra');
      const pct = document.getElementById('onb-pct');
      const txt = document.getElementById('onb-texto');
      const card = document.getElementById('onb-card');
      if (barra) barra.style.width = d.percentual + '%';
      if (pct) pct.textContent = d.percentual + '%';
      if (txt) {{
        const pend = (d.passos || []).filter(p => !p.concluido).map(p => p.titulo);
        txt.textContent = d.completo
          ? 'Tudo pronto. Abra para revisar ou testar novamente no simulador.'
          : 'Falta: ' + pend.join(' | ');
      }}
      if (card && d.completo) {{
        card.style.borderLeftColor = 'var(--p-turquesa, #3ccbc5)';
        if (pct) pct.style.color = 'var(--p-turquesa-texto, #0b7570)';
      }}
    }} catch (e) {{
      console.error('[Presenthia F1.3] Erro ao carregar onboarding:', e);
    }}
  }}

'''

def aplicar_conta():
    txt = CONTA.read_text(encoding="utf-8")
    if MARCA in txt:
        print("[conta_tenant] ja contem o card -- nada a fazer.")
        return False
    orig = txt

    m = re.search(r'[ \t]*<!--\s*CONFIGURA\S*\s+GUIADA\s+E\s+ATALHOS[^>]*-->', txt)
    if not m:
        sys.exit("ERRO: ancora do comentario 'CONFIGURACAO GUIADA E ATALHOS RAPIDOS' nao encontrada.")
    txt = txt[:m.start()] + CARD + txt[m.start():]

    txt, n = re.subn(r'(\n([ \t]*)atualizarCardsAtencao\(\);\n)([ \t]*return;)',
                     lambda mm: mm.group(1) + mm.group(2) + 'atualizarOnboardingInicio();\n' + mm.group(3),
                     txt, count=1)
    if n != 1:
        sys.exit("ERRO: ancora 'atualizarCardsAtencao(); return;' nao encontrada.")

    txt, n = re.subn(r'async function carregarView\(nome, el\) \{\{',
                     lambda mm: FUNCAO_JS + mm.group(0), txt, count=1)
    if n != 1:
        sys.exit("ERRO: ancora 'async function carregarView' nao encontrada.")

    (RAIZ / "_bak").mkdir(exist_ok=True)
    shutil.copy2(CONTA, RAIZ / f"_bak/conta_tenant.py.{CARIMBO}")
    CONTA.write_text(txt, encoding="utf-8")
    print(f"[conta_tenant] aplicado (+{len(txt)-len(orig)} bytes).")
    return True

def aplicar_main():
    txt = MAIN.read_text(encoding="utf-8")
    if "bytes invalidos" in txt:
        print("[main] loader ja blindado -- nada a fazer.")
        return False
    novo = (
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
    padrao = (r'[ \t]*with open\(target_file, "r", encoding="utf-8"\) as f:\n'
              r'[ \t]*from fastapi\.responses import HTMLResponse\n'
              r'[ \t]*return HTMLResponse\(content=f\.read\(\), media_type="text/html"\)\n')
    txt, n = re.subn(padrao, lambda mm: novo, txt, count=1)
    if n != 1:
        print("[main] AVISO: bloco open() nao casou; loader NAO blindado.")
        return False
    (RAIZ / "_bak").mkdir(exist_ok=True)
    shutil.copy2(MAIN, RAIZ / f"_bak/main.py.{CARIMBO}")
    MAIN.write_text(txt, encoding="utf-8")
    print("[main] loader blindado contra UnicodeDecodeError.")
    return True

a = aplicar_conta()
b = aplicar_main()
for f in (CONTA, MAIN):
    py_compile.compile(str(f), doraise=True)
print("\nOK: sintaxe valida em ambos os arquivos.")
print("Alterado:", ("conta_tenant.py " if a else "") + ("main.py" if b else ""))
