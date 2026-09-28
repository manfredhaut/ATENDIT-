#!/usr/bin/env python3
"""Envolve ia_config.html e empresa_cadastro.html com uma barra de sub-abas,
SEM alterar o conteudo interno de nenhum dos dois -- apenas os cerca com um
wrapper e injeta a segunda sub-aba sob demanda via fetch('/dashboards/...'),
o MESMO padrao ja usado e comprovado em canais.html (injetarModulo).
Idempotente (checa marcador antes de aplicar)."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
DASH = RAIZ / "backend/app/frontend/dashboards"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)


def envolver(arquivo, marca, pre, pos):
    alvo = DASH / arquivo
    atual = alvo.read_text(encoding="utf-8")
    if marca in atual:
        print(f"[{arquivo}] ja aplicado -- nada a fazer.")
        return
    shutil.copy2(alvo, bak / f"{arquivo}.{CARIMBO}")
    alvo.write_text(pre + atual + pos, encoding="utf-8")
    print(f"[{arquivo}] aplicado. Backup em _bak/{arquivo}.{CARIMBO}")

PRE_IA = '''<div class="fade-in" style="display:flex; flex-direction:column; gap:18px;">
  <div style="display:flex; gap:10px; border-bottom:1px solid var(--p-borda, #eae3dc); padding-bottom:2px;">
    <button id="ia-tab-btn-persona" onclick="iaAbrirSubaba('persona')" style="padding:10px 18px; background:var(--p-turquesa-texto, #0b7570); color:#ffffff; border:none; border-radius:8px 8px 0 0; font-weight:700; font-size:0.85rem; cursor:pointer;">Persona e Tom</button>
    <button id="ia-tab-btn-modelos" onclick="iaAbrirSubaba('modelos')" style="padding:10px 18px; background:#ffffff; color:var(--p-texto-suave, #5e5563); border:1px solid var(--p-borda, #eae3dc); border-bottom:none; border-radius:8px 8px 0 0; font-weight:600; font-size:0.85rem; cursor:pointer;">Modelos e Chaves</button>
  </div>
  <div id="ia-view-persona">
'''
POS_IA = '''
  </div>
  <div id="ia-view-modelos" style="display:none;"></div>
</div>
<script>
(function() {
  "use strict";
  var carregadoModelos = false;

  window.iaAbrirSubaba = async function(aba) {
    var vPersona = document.getElementById('ia-view-persona');
    var vModelos = document.getElementById('ia-view-modelos');
    var bPersona = document.getElementById('ia-tab-btn-persona');
    var bModelos = document.getElementById('ia-tab-btn-modelos');
    if (!vPersona || !vModelos || !bPersona || !bModelos) return;

    var ativo = { background: 'var(--p-turquesa-texto, #0b7570)', color: '#ffffff', borderColor: 'var(--p-turquesa-texto, #0b7570)', fontWeight: '700' };
    var inativo = { background: '#ffffff', color: 'var(--p-texto-suave, #5e5563)', borderColor: 'var(--p-borda, #eae3dc)', fontWeight: '600' };

    if (aba === 'persona') {
      vPersona.style.display = 'block'; vModelos.style.display = 'none';
      Object.assign(bPersona.style, ativo); Object.assign(bModelos.style, inativo);
    } else {
      vPersona.style.display = 'none'; vModelos.style.display = 'block';
      Object.assign(bModelos.style, ativo); Object.assign(bPersona.style, inativo);
      if (!carregadoModelos) {
        carregadoModelos = true;
        try {
          var res = await fetch('/dashboards/gemini_config');
          if (!res.ok) throw new Error('HTTP ' + res.status);
          var txt = await res.text();
          var html; try { html = JSON.parse(txt); } catch (_) { html = txt; }
          vModelos.innerHTML = html;
          vModelos.querySelectorAll('script').forEach(function(antigo) {
            var novo = document.createElement('script');
            if (antigo.src) novo.src = antigo.src; else novo.textContent = antigo.textContent;
            antigo.replaceWith(novo);
          });
        } catch (e) {
          vModelos.innerHTML = '<div style="padding:20px; color:#b91c1c; font-weight:600;">Falha ao carregar Modelos e Chaves: ' + e.message + '</div>';
        }
      }
    }
  };
})();
</script>
'''

PRE_EMP = '''<div class="fade-in" style="display:flex; flex-direction:column; gap:18px;">
  <div style="display:flex; gap:10px; border-bottom:1px solid var(--p-borda, #eae3dc); padding-bottom:2px;">
    <button id="emp-tab-btn-perfil" onclick="empAbrirSubaba('perfil')" style="padding:10px 18px; background:var(--p-turquesa-texto, #0b7570); color:#ffffff; border:none; border-radius:8px 8px 0 0; font-weight:700; font-size:0.85rem; cursor:pointer;">Perfil</button>
    <button id="emp-tab-btn-usuarios" onclick="empAbrirSubaba('usuarios')" style="padding:10px 18px; background:#ffffff; color:var(--p-texto-suave, #5e5563); border:1px solid var(--p-borda, #eae3dc); border-bottom:none; border-radius:8px 8px 0 0; font-weight:600; font-size:0.85rem; cursor:pointer;">Usuários e Permissões</button>
  </div>
  <div id="emp-view-perfil">
'''
POS_EMP = '''
  </div>
  <div id="emp-view-usuarios" style="display:none;"></div>
</div>
<script>
(function() {
  "use strict";
  var carregadoUsuarios = false;

  window.empAbrirSubaba = async function(aba) {
    var vPerfil = document.getElementById('emp-view-perfil');
    var vUsuarios = document.getElementById('emp-view-usuarios');
    var bPerfil = document.getElementById('emp-tab-btn-perfil');
    var bUsuarios = document.getElementById('emp-tab-btn-usuarios');
    if (!vPerfil || !vUsuarios || !bPerfil || !bUsuarios) return;

    var ativo = { background: 'var(--p-turquesa-texto, #0b7570)', color: '#ffffff', borderColor: 'var(--p-turquesa-texto, #0b7570)', fontWeight: '700' };
    var inativo = { background: '#ffffff', color: 'var(--p-texto-suave, #5e5563)', borderColor: 'var(--p-borda, #eae3dc)', fontWeight: '600' };

    if (aba === 'perfil') {
      vPerfil.style.display = 'block'; vUsuarios.style.display = 'none';
      Object.assign(bPerfil.style, ativo); Object.assign(bUsuarios.style, inativo);
    } else {
      vPerfil.style.display = 'none'; vUsuarios.style.display = 'block';
      Object.assign(bUsuarios.style, ativo); Object.assign(bPerfil.style, inativo);
      if (!carregadoUsuarios) {
        carregadoUsuarios = true;
        try {
          var res = await fetch('/dashboards/equipe');
          if (!res.ok) throw new Error('HTTP ' + res.status);
          var txt = await res.text();
          var html; try { html = JSON.parse(txt); } catch (_) { html = txt; }
          vUsuarios.innerHTML = html;
          vUsuarios.querySelectorAll('script').forEach(function(antigo) {
            var novo = document.createElement('script');
            if (antigo.src) novo.src = antigo.src; else novo.textContent = antigo.textContent;
            antigo.replaceWith(novo);
          });
        } catch (e) {
          vUsuarios.innerHTML = '<div style="padding:20px; color:#b91c1c; font-weight:600;">Falha ao carregar Usuários e Permissões: ' + e.message + '</div>';
        }
      }
    }
  };
})();
</script>
'''

envolver("ia_config.html", "ia-view-persona", PRE_IA, POS_IA)
envolver("empresa_cadastro.html", "emp-view-perfil", PRE_EMP, POS_EMP)
