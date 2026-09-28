#!/usr/bin/env python3
"""F1.0 - gera docs/PARIDADE.gerado.md a partir do codigo. So le; so grava em docs/."""
import re, sys
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
APP = RAIZ / "backend/app"
DASH = APP / "frontend/dashboards"
CONTA = APP / "routes/conta_tenant.py"

DESTINO = {
    "inicio": "1 Início", "configuracao_guiada": "1 Início › Configuração guiada",
    "fila_atendimento": "2 Atendimento", "leads": "3 Aquisição / 4 CRM & Funil (provisório: mesma view)",
    "calendar_config": "5 Agenda (+ 6 Profissionais & Escalas)", "equipe": "14 Empresa & Conta › Usuários e permissões (F1.7)",
    "ecommerce_config": "7 Vitrine & Loja", "video": "8 Consultoria por Vídeo",
    "ia_config": "9 Assistente IA › Persona e tom", "gemini_config": "9 Assistente IA › Modelos e chaves",
    "rag_management": "10 Base de Conhecimento", "canais": "11 Canais",
    "link_whatsapp": "11 Canais › WhatsApp QR Code", "meta_config": "11 Canais › WhatsApp Oficial",
    "faturamento": "12 Financeiro", "intel_operacional": "13 Inteligência Comercial & Operacional",
    "empresa_cadastro": "14 Empresa & Conta", "modelos_segmento": "F1.4 Modelos por segmento",
}

def bloco(txt, nome):
    m = re.search(nome + r"\s*=\s*'''(.*?)'''", txt, re.S)
    return set(re.findall(r'data-v="([^"]+)"', m.group(1))) if m else set()

src = CONTA.read_text(encoding="utf-8", errors="replace")
legado, v2 = bloco(src, "menu_legado_html"), bloco(src, "menu_v2_html")

chamadas = {}
fontes = [(p, p.read_text(encoding="utf-8", errors="replace")) for p in DASH.glob("*.html")]
fontes.append((CONTA, src))
for p, t in fontes:
    for alvo in re.findall(r"(?:carregarView|navegarParaPasso|injetarModulo)\(\s*['\"]([\w-]+)['\"]", t):
        chamadas.setdefault(alvo, set()).add(p.stem)

linhas = ["# PARIDADE ATENDIT -> Presenthia (gerado do codigo; revisar tela a tela)", "",
          "Legenda: L = no menu legado, V2 = no menu novo, Ref = aberta por outra tela.", "",
          "| View | Titulo | L | V2 | Ref | Rotas chamadas | Destino Presenthia | Status |",
          "|---|---|---|---|---|---|---|---|"]
alertas = []
for p in sorted(DASH.glob("*.html")):
    n = p.stem
    t = p.read_text(encoding="utf-8", errors="replace")
    h = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", t, re.S)
    titulo = re.sub(r"<[^>]+>|\s+", " ", h.group(1)).strip()[:45] if h else "-"
    rotas = sorted(set(re.findall(r"fetch\(\s*[`'\"](/[^`'\"?$]*)", t)))
    ref = sorted(chamadas.get(n, set()) - {n})
    inL, inV = n in legado, n in v2
    status = "conferir"
    if inL and not inV and not ref:
        status = "!! SEM ENTRADA NO MENU NOVO"
        alertas.append(n)
    elif not inL and not inV and not ref:
        status = "orfa (nao acessivel)"
    linhas.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        n, titulo, "x" if inL else "", "x" if inV else "", ",".join(ref) or "",
        "<br>".join(rotas[:6]) or "-", DESTINO.get(n, "?"), status))

linhas += ["", "## Alertas", ""] + (["- `%s`: existe no menu legado e nao tem caminho no menu novo." % a for a in alertas] or ["- nenhum"])
out = RAIZ / "docs"; out.mkdir(exist_ok=True)
(out / "PARIDADE.gerado.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
print("\n".join(linhas))
