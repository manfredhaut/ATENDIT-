#!/usr/bin/env python3
"""calendar_config.html: a aba 'Motor AS-IS -> TO-BE' so listava/removia
da lista de espera -- sem jeito de ADICIONAR um cliente manualmente nem
de ENVIAR A OFERTA de vaga (as duas rotas ja existiam no backend, sem
nenhum botao ligado a elas). Acrescenta:
1) formulario simples para adicionar cliente a espera (servico opcional,
   o backend ja resolve o servico ativo do tenant sozinho);
2) botao 'Enviar Oferta' em cada linha, reenviando a propria janela que
   o cliente pediu para o disparo assincrono via Celery.
Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/calendar_config.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "calAbrirFormNovaEspera" in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ANCORA_HTML = '<div id="tobe-lista-sugestoes" style="display:grid; gap:10px; margin-top:16px;">'
NOVO_HTML = '''<div id="tobe-form-espera" style="display:none; background:#F8FAFC; border:1px solid #CBD5E1; border-radius:8px; padding:14px; margin-bottom:12px;">
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                        <input type="text" id="espera-nome" placeholder="Nome do cliente" style="padding:8px; border-radius:6px; border:1px solid #CBD5E1; font-size:0.85rem;">
                        <input type="text" id="espera-fone" placeholder="Telefone / WhatsApp" style="padding:8px; border-radius:6px; border:1px solid #CBD5E1; font-size:0.85rem;">
                    </div>
                    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:10px;">
                        <input type="date" id="espera-data" style="padding:8px; border-radius:6px; border:1px solid #CBD5E1; font-size:0.85rem;">
                        <input type="time" id="espera-hora" value="09:00" style="padding:8px; border-radius:6px; border:1px solid #CBD5E1; font-size:0.85rem;">
                        <input type="number" id="espera-duracao" value="30" min="10" step="5" placeholder="Duração (min)" style="padding:8px; border-radius:6px; border:1px solid #CBD5E1; font-size:0.85rem;">
                    </div>
                    <div style="display:flex; justify-content:flex-end; gap:8px;">
                        <button type="button" onclick="calFecharFormNovaEspera()" style="padding:7px 14px; border:1px solid #CBD5E1; background:#fff; border-radius:6px; font-weight:600; cursor:pointer; font-size:0.85rem;">Cancelar</button>
                        <button type="button" onclick="calSalvarNovaEspera()" style="padding:7px 16px; background:#059669; color:#fff; border:none; border-radius:6px; font-weight:700; cursor:pointer; font-size:0.85rem;">Adicionar à Espera</button>
                    </div>
                </div>
                <button type="button" onclick="calAbrirFormNovaEspera()" style="margin-bottom:12px; padding:8px 14px; background:#fff; border:1px solid #059669; color:#059669; border-radius:6px; font-weight:700; cursor:pointer; font-size:0.85rem;">+ Adicionar cliente à espera</button>
                <div id="tobe-lista-sugestoes" style="display:grid; gap:10px; margin-top:16px;">'''

if ANCORA_HTML not in txt:
    sys.exit("ERRO: ancora HTML 'tobe-lista-sugestoes' nao encontrada.")
txt = txt.replace(ANCORA_HTML, NOVO_HTML, 1)

ANCORA_JS = "        async function calRemoverWaitlist(waitlistId) {"
NOVO_JS = '''        function calAbrirFormNovaEspera() {
            var f = document.getElementById('tobe-form-espera');
            if (f) f.style.display = 'block';
        }

        function calFecharFormNovaEspera() {
            var f = document.getElementById('tobe-form-espera');
            if (f) f.style.display = 'none';
        }

        async function calSalvarNovaEspera() {
            var nome = (document.getElementById('espera-nome').value || '').trim();
            var fone = (document.getElementById('espera-fone').value || '').trim();
            var data = document.getElementById('espera-data').value;
            var hora = document.getElementById('espera-hora').value;
            var duracaoMin = parseInt(document.getElementById('espera-duracao').value, 10) || 30;
            if (!nome || !fone || !data || !hora) {
                alert('Preencha nome, telefone, data e horário desejados.');
                return;
            }
            var inicio = new Date(data + 'T' + hora + ':00');
            var fim = new Date(inicio.getTime() + duracaoMin * 60000);
            try {
                var r = await fetch('/calendar/waitlist/' + window.currentTenantSlug, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        customer_name: nome,
                        customer_phone: fone,
                        desired_start: inicio.toISOString(),
                        desired_end: fim.toISOString()
                    })
                });
                if (!r.ok) {
                    var corpo = await r.json().catch(function() { return {}; });
                    throw new Error(corpo.detail || ('HTTP ' + r.status));
                }
                calFecharFormNovaEspera();
                document.getElementById('espera-nome').value = '';
                document.getElementById('espera-fone').value = '';
                await calCarregarAsIsToBe();
                alert('Cliente adicionado à lista de espera.');
            } catch (e) {
                alert('Falha ao adicionar cliente: ' + e.message);
            }
        }

        async function calEnviarOferta(serviceTypeId, inicioIso, fimIso) {
            if (!confirm('Enviar oferta desta vaga para o cliente prioritário da fila?')) return;
            try {
                var r = await fetch('/calendar/waitlist/' + window.currentTenantSlug + '/disparar-oferta', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ service_type_id: serviceTypeId, inicio_iso: inicioIso, fim_iso: fimIso })
                });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                alert('Oferta enviada. O cliente será notificado em instantes.');
                await calCarregarAsIsToBe();
            } catch (e) {
                alert('Falha ao enviar oferta: ' + e.message);
            }
        }

        async function calRemoverWaitlist(waitlistId) {'''

if ANCORA_JS not in txt:
    sys.exit("ERRO: ancora JS 'async function calRemoverWaitlist' nao encontrada.")
txt = txt.replace(ANCORA_JS, NOVO_JS, 1)
ANCORA_BOTAO = "+ '    <button type=\"button\" onclick=\"calRemoverWaitlist(\\'' + item.id + '\\')\" style=\"padding:6px 12px; background:#FEE2E2; color:#991B1B; border:1px solid #FECACA; border-radius:6px; font-size:0.8rem; font-weight:600; cursor:pointer;\">Remover</button>'"
NOVO_BOTAO = ("+ '    <button type=\"button\" onclick=\"calEnviarOferta(\\'' + item.service_type_id + '\\', \\'' + item.desired_start_iso + '\\', \\'' + item.desired_end_iso + '\\')\" "
              "style=\"padding:6px 12px; background:#D1FAE5; color:#065F46; margin-right:6px;\">Enviar Oferta</button>'\n"
              "                          " + ANCORA_BOTAO)
if ANCORA_BOTAO not in txt:
    sys.exit("ERRO: ancora do botao Remover (para inserir Enviar Oferta ao lado) nao encontrada.")
txt = txt.replace(ANCORA_BOTAO, NOVO_BOTAO, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"calendar_config.html.{CARIMBO}")
ALVO.write_text(txt, encoding="utf-8")
print(f"Aplicado. Backup em _bak/calendar_config.html.{CARIMBO}")
