#!/usr/bin/env python3
"""Cria dashboards/aviso_fase3_profissionais.html: pagina honesta para o menu
item '6. Profissionais & Escalas' (a funcionalidade real e F3.11, ainda nao
construida). Substitui o que antes abria 'equipe' diretamente; a tela de
equipe/permissoes agora vive como sub-aba dentro de Empresa & Conta.
So cria o arquivo se ele nao existir (idempotente por natureza)."""
import sys
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/aviso_fase3_profissionais.html"

CONTEUDO = '''<!-- PRESENTHIA - ABA 6 PROFISSIONAIS & ESCALAS (placeholder honesto ate a Fase 3: F3.11) -->
<div class="fade-in" style="display:flex; flex-direction:column; gap:20px; max-width:900px;">
  <div style="background:#ffffff; padding:26px 30px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc); border-left:4px solid var(--p-ambar, #e3a028);">
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <h2 style="font-family:var(--p-fonte-titulo, Georgia, serif); font-size:1.5rem; color:var(--p-texto, #231726); margin:0;">Profissionais &amp; Escalas</h2>
      <span style="background:#fdf1d8; color:#8a5a00; font-size:11px; font-weight:700; padding:4px 12px; border-radius:999px;">Em construção · Fase 3</span>
    </div>
    <p style="font-size:0.9rem; color:var(--p-texto-suave, #5e5563); margin:10px 0 0 0;">Esta aba vai reunir o cadastro de profissionais, seus horários de trabalho e escalas — para o orquestrador de agendamento composto (múltiplos profissionais num mesmo atendimento). Ainda não está pronta.</p>
  </div>

  <div style="background:#ffffff; padding:18px 22px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc); display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;">
    <p style="font-size:0.85rem; color:var(--p-texto-suave, #5e5563); margin:0; flex:1; min-width:220px;">O que já existe hoje — cadastro de usuários da equipe e seus níveis de acesso (Dono, Gestor, Operador, Financeiro, Leitura) — está em Empresa &amp; Conta.</p>
    <button onclick="carregarView('empresa_cadastro', document.querySelector('[data-v=empresa_cadastro]'))" style="padding:10px 18px; background:var(--p-magenta, #D0006F); color:#ffffff; border:none; border-radius:10px; font-weight:700; font-size:0.85rem; cursor:pointer;">Abrir Empresa &amp; Conta →</button>
  </div>
</div>
'''

if ALVO.exists():
    print("Arquivo ja existe -- nada a fazer (nao sobrescrevo silenciosamente).")
else:
    ALVO.write_text(CONTEUDO, encoding="utf-8")
    print("Criado:", ALVO)
