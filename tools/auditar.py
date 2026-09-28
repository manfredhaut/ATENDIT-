#!/usr/bin/env python3
"""Auditoria de honestidade (so le). Gera docs/AUDITORIA.gerado.md. Heuristica: serve para TRIAGEM."""
import ast, re, sys
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
APP = RAIZ / "backend/app"
AUTH = re.compile(r"exigir_acesso|sessao_do_tenant|sessao_admin|sessao_tenant|sessao_ativa|obter_perfil_sessao|"
                  r"Depends\((?!get_db)|hmac|INTERNAL_API_TOKEN|WEBHOOK_SECRET|X-Internal|verificar_")
OK_CALL = re.compile(r"_autz|exigir|sessao|JSONResponse|HTMLResponse|Response|HTTPException|logger|logging|"
                     r"^str$|^int$|^len$|^dict$|^list$|\.get$|^request\.|\.json$|\.strip$|\.lower$|isinstance")
SUCESSO = re.compile(r"sucesso|success|\"ok\"\s*:\s*True", re.I)
VERBOS = ("get", "post", "put", "delete", "patch", "api_route")

mw = set()
pa = APP / "core/panel_auth.py"
if pa.exists():
    mw = set(re.findall(r"[\"'](/[^\"']*)[\"']", pa.read_text(encoding="utf-8", errors="replace")))

sem_auth, fixas = [], []
for f in sorted(APP.rglob("*.py")):
    if "_bak" in f.parts or "__pycache__" in f.parts:
        continue
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        rota = None
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in VERBOS \
               and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                rota = "%s %s" % (d.func.attr.upper(), d.args[0].value)
                caminho = d.args[0].value
        if not rota:
            continue
        seg = ast.get_source_segment(src, fn) or ""
        loc = "%s:%d" % (f.relative_to(APP), fn.lineno)
        if not AUTH.search(seg) and not any(caminho.startswith(p) for p in mw if len(p) > 1):
            sem_auth.append((rota, loc))
        chamadas = {ast.unparse(n.func) for st in fn.body for n in ast.walk(st) if isinstance(n, ast.Call)}
        uteis = {c for c in chamadas if not OK_CALL.search(c)}
        if not uteis and SUCESSO.search(seg) and not re.search(r"health|ping|openapi|/docs", caminho):
            fixas.append((rota, loc))

html = []
for f in sorted((APP / "frontend").rglob("*.html")):
    t = f.read_text(encoding="utf-8", errors="replace")
    ach = []
    if re.search(r"setTimeout\(.{0,400}?(Salv|sucesso|Sucesso)", t, re.S): ach.append("salvar simulado (setTimeout)")
    if "Math.random(" in t: ach.append("token/valor aleatorio no navegador")
    if "eval(" in t: ach.append("eval()")
    if re.search(r"Assinatura Ativa|Plano Professional|Inerte|Gateway IoT", t): ach.append("status/valor fixo de mockup")
    if t.count("onclick=") >= 1 and t.count("fetch(") == 0: ach.append("botoes sem nenhuma chamada ao servidor")
    if ach: html.append((str(f.relative_to(APP / "frontend")), ach))

marc = []
for f in list(APP.rglob("*.py")) + list((APP / "frontend").rglob("*.html")):
    if "_bak" in f.parts or "__pycache__" in f.parts: continue
    n = len(re.findall(r"mock|demo|simul|placeholder|fake|TODO|FIXME|hardcod", f.read_text(encoding="utf-8", errors="replace"), re.I))
    if n: marc.append((n, str(f.relative_to(APP))))
marc.sort(reverse=True)

L = ["# AUDITORIA (gerada do codigo; triagem, nao veredito)", "",
     "## 1. Rotas com resposta fixa (nao consultam banco, IA nem servico)", ""]
L += ["- `%s` (%s)" % x for x in fixas] or ["- nenhuma"]
L += ["", "## 2. Telas com padrao de simulacao", ""]
L += ["- `%s`: %s" % (a, "; ".join(b)) for a, b in html] or ["- nenhuma"]
L += ["", "## 3. Rotas sem verificacao de sessao visivel (revisar se deveriam ser publicas)", ""]
L += ["- `%s` (%s)" % x for x in sem_auth] or ["- nenhuma"]
L += ["", "## 4. Arquivos com mais marcadores mock/demo/TODO", ""]
L += ["- %d x `%s`" % x for x in marc[:15]] or ["- nenhum"]
(RAIZ / "docs").mkdir(exist_ok=True)
(RAIZ / "docs/AUDITORIA.gerado.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print("\n".join(L))
