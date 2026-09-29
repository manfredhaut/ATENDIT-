#!/usr/bin/env python3
"""rag_management.html: os botoes 'Processar e Injetar RAG' chamavam
window.handleFileChange() e window.processarEInjetarRAG(), que nao eram
definidas em lugar nenhum do arquivo -- clique nao fazia NADA (nem erro
visivel para o usuario, so um ReferenceError silencioso no console).

O backend (POST /v1/rag/upload/{tenant_id}) sempre funcionou; faltava
so a ponte do frontend. Idempotente."""
import shutil, sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/frontend/dashboards/rag_management.html"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
MARCA = "handleFileChange = function"

txt = ALVO.read_text(encoding="utf-8")
if MARCA in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ANCORA_BOTAO = 'onclick="window.processarEInjetarRAG()"'
NOVO_BOTAO = 'onclick="window.processarEInjetarRAG(event)"'
if ANCORA_BOTAO not in txt:
    sys.exit("ERRO: ancora do onclick do botao 'Processar e Injetar RAG' nao encontrada.")

ANCORA_SCRIPT = "    window.excluirDocRAG = async function(docId) {"
if ANCORA_SCRIPT not in txt:
    sys.exit("ERRO: ancora 'window.excluirDocRAG = async function' nao encontrada.")

NOVO_BLOCO = '''    var arquivoSelecionadoRAG = null;

    window.handleFileChange = function(input) {
        arquivoSelecionadoRAG = (input.files && input.files[0]) ? input.files[0] : null;
    };

    window.processarEInjetarRAG = async function(ev) {
        if (!arquivoSelecionadoRAG) {
            alert("Escolha um arquivo antes de processar.");
            return;
        }
        const tenantId = obterTenantId();
        if (!tenantId) {
            alert("Nao foi possivel identificar a empresa. Recarregue a pagina.");
            return;
        }
        const botao = ev ? ev.currentTarget : null;
        const textoOriginal = botao ? botao.innerHTML : null;
        if (botao) { botao.disabled = true; botao.innerHTML = "Processando..."; }
        try {
            const dados = new FormData();
            dados.append("file", arquivoSelecionadoRAG);
            const res = await fetch("/v1/rag/upload/" + tenantId, { method: "POST", body: dados });
            if (!res.ok) {
                const corpo = await res.json().catch(function() { return {}; });
                throw new Error(corpo.detail || ("HTTP " + res.status));
            }
            arquivoSelecionadoRAG = null;
            const campoArquivo = document.getElementById("rag-file-input");
            if (campoArquivo) campoArquivo.value = "";
            await carregarDocumentos();
            alert("Documento enviado e em processamento na base de conhecimento.");
        } catch (e) {
            alert("Falha ao processar documento: " + e.message);
        } finally {
            if (botao) { botao.disabled = false; botao.innerHTML = textoOriginal; }
        }
    };

''' + ANCORA_SCRIPT

novo_txt = txt.replace(ANCORA_BOTAO, NOVO_BOTAO, 1)
novo_txt = novo_txt.replace(ANCORA_SCRIPT, NOVO_BLOCO, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag_management.html.{CARIMBO}")
ALVO.write_text(novo_txt, encoding="utf-8")
print(f"Aplicado. Backup em _bak/rag_management.html.{CARIMBO}")
