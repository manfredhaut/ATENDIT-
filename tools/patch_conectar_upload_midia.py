#!/usr/bin/env python3
"""rag_management.html: o botao 'Subir Midia de Envio' chamava
window.uploadMidiaOperacional(), que nao era definida em lugar nenhum --
clique nao fazia nada. O backend (POST /v1/ecommerce/upload-media/{tenant})
sempre funcionou; faltava a ponte do frontend.

Limitacao honesta mantida na UI: nao existe rota GET nem tabela no banco
para listar midias ja enviadas, entao a lista mostrada e so da sessao
atual do navegador -- recarregar a pagina zera a lista. O placeholder
inicial ('Sincronizando...') mentia que algo estava sendo buscado; troca
por um aviso honesto disso. Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/rag_management.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
MARCA = "uploadMidiaOperacional = async function"

txt = ALVO.read_text(encoding="utf-8")
if MARCA in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ANCORA_BOTAO = 'onclick="window.uploadMidiaOperacional()"'
NOVO_BOTAO = 'onclick="window.uploadMidiaOperacional(event)"'
if ANCORA_BOTAO not in txt:
    sys.exit("ERRO: ancora do onclick do botao 'Subir Midia de Envio' nao encontrada.")

ANCORA_PLACEHOLDER = "Sincronizando duto de arquivos operacionais..."
NOVO_PLACEHOLDER = "Nenhum arquivo enviado nesta sessao. O historico ainda nao e salvo entre acessos."
if ANCORA_PLACEHOLDER not in txt:
    sys.exit("ERRO: texto do placeholder da tabela de midia nao encontrado.")

ANCORA_TBODY = '<tbody id="media-documents-table-body">'
NOVO_TBODY = '<tbody id="media-documents-table-body" data-vazio="1">'
if ANCORA_TBODY not in txt:
    sys.exit("ERRO: tbody 'media-documents-table-body' nao encontrado.")

ANCORA_SCRIPT = "    window.excluirDocRAG = async function(docId) {"
if ANCORA_SCRIPT not in txt:
    sys.exit("ERRO: ancora 'window.excluirDocRAG = async function' nao encontrada (ponto de insercao).")

NOVO_BLOCO = '''    window.uploadMidiaOperacional = async function(ev) {
        const input = document.getElementById("media-file-input");
        const arquivo = input && input.files && input.files[0];
        if (!arquivo) {
            alert("Escolha um arquivo antes de enviar.");
            return;
        }
        const tenantId = obterTenantId();
        if (!tenantId) {
            alert("Nao foi possivel identificar a empresa. Recarregue a pagina.");
            return;
        }
        const botao = ev ? ev.currentTarget : null;
        const textoOriginal = botao ? botao.innerHTML : null;
        if (botao) { botao.disabled = true; botao.innerHTML = "Enviando..."; }
        try {
            const dados = new FormData();
            dados.append("file", arquivo);
            const res = await fetch("/v1/ecommerce/upload-media/" + tenantId, { method: "POST", body: dados });
            if (!res.ok) {
                const corpo = await res.json().catch(function() { return {}; });
                throw new Error(corpo.detail || ("HTTP " + res.status));
            }
            const resultado = await res.json();
            const corpoTabela = document.getElementById("media-documents-table-body");
            if (corpoTabela) {
                if (corpoTabela.dataset.vazio === "1") {
                    corpoTabela.innerHTML = "";
                    corpoTabela.dataset.vazio = "0";
                }
                const tr = document.createElement("tr");
                const urlAbsoluta = window.location.origin + resultado.url;
                tr.innerHTML = "<td>" + arquivo.name + "</td>" +
                               "<td>" + (resultado.is_video ? "Video" : "Imagem") + "</td>" +
                               "<td style=\\"text-align:center;\\"><button onclick=\\"navigator.clipboard.writeText('" + urlAbsoluta + "'); alert('Link copiado!')\\" style=\\"padding:6px 12px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; cursor:pointer;\\">Copiar link</button></td>";
                corpoTabela.appendChild(tr);
            }
            input.value = "";
        } catch (e) {
            alert("Falha ao enviar arquivo: " + e.message);
        } finally {
            if (botao) { botao.disabled = false; botao.innerHTML = textoOriginal; }
        }
    };

''' + ANCORA_SCRIPT

novo_txt = txt.replace(ANCORA_BOTAO, NOVO_BOTAO, 1)
novo_txt = novo_txt.replace(ANCORA_PLACEHOLDER, NOVO_PLACEHOLDER, 1)
novo_txt = novo_txt.replace(ANCORA_TBODY, NOVO_TBODY, 1)
novo_txt = novo_txt.replace(ANCORA_SCRIPT, NOVO_BLOCO, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag_management.html.{CARIMBO}")
ALVO.write_text(novo_txt, encoding="utf-8")
print(f"Aplicado. Backup em _bak/rag_management.html.{CARIMBO}")
