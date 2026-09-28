#!/usr/bin/env python3
"""Substitui o mockup do Financeiro (dados falsos, botao que finge salvar) por tela honesta."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/faturamento.html"

NOVO = '''<!-- PRESENTHIA - ABA 12 FINANCEIRO (placeholder honesto ate a Fase 4: F4.5 recebimentos, F4.7 assinatura) -->
<div class="fade-in" style="display:flex; flex-direction:column; gap:20px; max-width:900px;">
  <div style="background:#ffffff; padding:26px 30px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc); border-left:4px solid var(--p-ambar, #e3a028);">
    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <h2 style="font-family:var(--p-fonte-titulo, Georgia, serif); font-size:1.5rem; color:var(--p-texto, #231726); margin:0;">Financeiro</h2>
      <span style="background:#fdf1d8; color:#8a5a00; font-size:11px; font-weight:700; padding:4px 12px; border-radius:999px;">Em construção · Fase 4</span>
    </div>
    <p style="font-size:0.9rem; color:var(--p-texto-suave, #5e5563); margin:10px 0 0 0;">Esta aba ainda não está ativa. Enquanto o módulo não fica pronto, nenhum valor, assinatura ou cobrança é exibido aqui, para não mostrar dados que não correspondem à sua conta.</p>
  </div>

  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:16px;">
    <div style="background:#ffffff; padding:18px 20px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc);">
      <div style="font-weight:700; font-size:0.92rem; color:var(--p-texto, #231726);">Recebimentos</div>
      <p style="font-size:0.8rem; color:var(--p-texto-suave, #5e5563); margin:6px 0 0 0;">Pix, cartão, boleto e link de pagamento do que seus clientes pagam a você.</p>
    </div>
    <div style="background:#ffffff; padding:18px 20px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc);">
      <div style="font-weight:700; font-size:0.92rem; color:var(--p-texto, #231726);">Formas de pagamento</div>
      <p style="font-size:0.8rem; color:var(--p-texto-suave, #5e5563); margin:6px 0 0 0;">Seu gateway atual, conta de recebimento ou chave Pix.</p>
    </div>
    <div style="background:#ffffff; padding:18px 20px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc);">
      <div style="font-weight:700; font-size:0.92rem; color:var(--p-texto, #231726);">Extrato</div>
      <p style="font-size:0.8rem; color:var(--p-texto-suave, #5e5563); margin:6px 0 0 0;">Movimentos e conciliação automática dos pagamentos.</p>
    </div>
    <div style="background:#ffffff; padding:18px 20px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc);">
      <div style="font-weight:700; font-size:0.92rem; color:var(--p-texto, #231726);">Assinatura da plataforma</div>
      <p style="font-size:0.8rem; color:var(--p-texto-suave, #5e5563); margin:6px 0 0 0;">Plano, faturas e consumo de IA e vídeo.</p>
    </div>
  </div>

  <div style="background:#ffffff; padding:18px 22px; border-radius:var(--p-raio, 16px); border:1px solid var(--p-borda, #eae3dc); display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;">
    <p style="font-size:0.85rem; color:var(--p-texto-suave, #5e5563); margin:0; flex:1; min-width:220px;">A configuração de gateways e Pix que já existe hoje continua na aba Vitrine &amp; Loja.</p>
    <button onclick="carregarView('ecommerce_config', document.querySelector('[data-v=ecommerce_config]'))" style="padding:10px 18px; background:var(--p-magenta, #D0006F); color:#ffffff; border:none; border-radius:10px; font-weight:700; font-size:0.85rem; cursor:pointer;">Abrir Vitrine &amp; Loja →</button>
  </div>
</div>
'''

atual = ALVO.read_text(encoding="utf-8", errors="replace")
if "salvarCadastroCompleto" not in atual and "Gateway IoT" not in atual:
    sys.exit("Nada a fazer: faturamento.html ja nao e o mockup (ou foi substituido antes).")
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
dest = bak / ("faturamento.html." + datetime.now().strftime("%Y%m%d_%H%M%S"))
shutil.copy2(ALVO, dest)
ALVO.write_text(NOVO, encoding="utf-8")
print("Substituido. Backup em", dest)
