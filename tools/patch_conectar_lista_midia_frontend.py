#!/usr/bin/env python3
"""rag_management.html: substitui o truque 'so nesta sessao' (data-vazio)
por uma lista de verdade, persistida (agora que existe a tabela
operational_media + rotas GET/DELETE). Adiciona carregarMidias() e
window.excluirMidia(), e um setTimeout para carregar ao abrir a tela,
igual ja acontece com o RAG. Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/rag_management.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "window.excluirMidia" in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ALVO_FUNC = '''    window.uploadMidiaOperacional = async function(ev) {
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
    };'''

NOVO_BLOCO = '''    async function carregarMidias() {
        const tenantId = obterTenantId();
        const corpo = document.getElementById("media-documents-table-body");
        if (!corpo || !tenantId) return;
        try {
            const resp = await fetch("/v1/ecommerce/media/" + tenantId, { headers: { "Accept": "application/json" } });
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            const itens = await resp.json();
            corpo.innerHTML = "";
            if (!itens || itens.length === 0) {
                corpo.innerHTML = '<tr><td colspan="3" style="padding: 20px; text-align: center; color: var(--p-texto-suave, #5e5563); font-style: italic;">Nenhum arquivo enviado ainda.</td></tr>';
                return;
            }
            itens.forEach(function(i) {
                const tr = document.createElement("tr");
                const urlAbsoluta = window.location.origin + i.url;
                tr.innerHTML = "<td>" + i.filename + "</td>" +
                               "<td>" + (i.file_type === "video" ? "Video" : "Imagem") + "</td>" +
                               "<td style=\\"text-align:center;\\"><button onclick=\\"navigator.clipboard.writeText('" + urlAbsoluta + "'); alert('Link copiado!')\\" style=\\"padding:6px 10px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; cursor:pointer; margin-right:6px;\\">Copiar link</button><button onclick=\\"window.excluirMidia('" + i.id + "')\\" style=\\"padding:6px 10px; border:1px solid #ef4444; border-radius:6px; background:#fff; color:#ef4444; cursor:pointer;\\">Excluir</button></td>";
                corpo.appendChild(tr);
            });
        } catch (e) {
            corpo.innerHTML = '<tr><td colspan="3" style="padding: 20px; text-align: center; color:#b91c1c;">Falha ao carregar arquivos: ' + e.message + '</td></tr>';
        }
    }

    window.excluirMidia = async function(mediaId) {
        if (!confirm("Excluir este arquivo?")) return;
        const tenantId = obterTenantId();
        try {
            const res = await fetch("/v1/ecommerce/media/" + tenantId + "/" + mediaId, { method: "DELETE" });
            if (!res.ok) throw new Error("HTTP " + res.status);
            await carregarMidias();
        } catch (e) {
            alert("Falha ao excluir: " + e.message);
        }
    };

    window.uploadMidiaOperacional = async function(ev) {
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
            input.value = "";
            await carregarMidias();
        } catch (e) {
            alert("Falha ao enviar arquivo: " + e.message);
        } finally {
            if (botao) { botao.disabled = false; botao.innerHTML = textoOriginal; }
        }
    };'''

if ALVO_FUNC not in txt:
    sys.exit("ERRO: bloco original de uploadMidiaOperacional nao encontrado no formato esperado.")
txt = txt.replace(ALVO_FUNC, NOVO_BLOCO, 1)

ALVO_SETTIMEOUT = "    window.recarregarDocumentosRAG = carregarDocumentos;\n    setTimeout(carregarDocumentos, 400);\n"
NOVO_SETTIMEOUT = ALVO_SETTIMEOUT + "    setTimeout(carregarMidias, 450);\n"
if ALVO_SETTIMEOUT not in txt:
    sys.exit("ERRO: bloco do setTimeout final nao encontrado.")
txt = txt.replace(ALVO_SETTIMEOUT, NOVO_SETTIMEOUT, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag_management.html.{CARIMBO}")
ALVO.write_text(txt, encoding="utf-8")
print(f"Aplicado. Backup em _bak/rag_management.html.{CARIMBO}")
