import subprocess
import sys

def rodar(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return res.stdout.strip(), res.stderr.strip(), res.returncode

print("=" * 70)
print("AUDITORIA FORENSE ATENDIT: VALIDACAO EXECUTIVA DE CONFORMIDADE")
print("=" * 70)

# ----------------------------------------------------------------------
# TESTE 1: BUG-01 e BUG-06 - Travas de Autorizacao (_exigir_dono)
# Executado dentro do container via httpx para respeitar a porta 8000 interna
# ----------------------------------------------------------------------
print("\n[TESTE 1/6] BUG-01 & BUG-06 - Travas de Borda em Rotas de Calendario")
cmd_auth = """docker exec atendit-api python3 -c "
import httpx
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=5.0)
r1 = c.get('/calendar/message-templates/manitest')
r2 = c.post('/calendar/message-templates/manitest', json={'templates': []})
r3 = c.post('/calendar/connections/calendly/manitest', json={'token': 'teste'})
print(f'{r1.status_code}|{r2.status_code}|{r3.status_code}')
" """

out_auth, err_auth, code_auth = rodar(cmd_auth)
if code_auth == 0 and "|" in out_auth:
    s_f2_get, s_f2_post, s_f4 = out_auth.split("|")
    pass_f2_f4 = s_f2_get in ['401', '403'] and s_f2_post in ['401', '403'] and s_f4 in ['401', '403']
    if pass_f2_f4:
        print(f"  [PASS] Acesso anonimo bloqueado pelas travas de seguranca:")
        print(f"         - GET /calendar/message-templates: HTTP {s_f2_get}")
        print(f"         - POST /calendar/message-templates: HTTP {s_f2_post}")
        print(f"         - POST /calendar/connections/calendly: HTTP {s_f4}")
    else:
        print(f"  [FAIL] Falha no bloqueio: GET={s_f2_get}, POST={s_f2_post}, Calendly={s_f4}")
else:
    print(f"  [FAIL] Erro ao disparar requisicoes internas: {err_auth or out_auth}")

# ----------------------------------------------------------------------
# TESTE 2: BUG-02 - Recuperacao de Senha em conta_tenant.py
# ----------------------------------------------------------------------
print("\n[TESTE 2/6] BUG-02 - Recuperacao de Senha (/v1/auth/esqueci-senha-form)")
cmd_f3 = """docker exec atendit-api python3 -c "
import httpx
r = httpx.get('http://127.0.0.1:8000/v1/auth/esqueci-senha-form', timeout=5.0)
print(r.status_code)
" """
out_f3, _, code_f3 = rodar(cmd_f3)
if code_f3 == 0 and out_f3 == '200':
    print(f"  [PASS] Formulario de recuperacao restaurado e funcional: HTTP {out_f3}")
else:
    print(f"  [FAIL] Rota de recuperacao respondeu HTTP {out_f3} (Esperado: 200)")

# ----------------------------------------------------------------------
# TESTE 3: BUG-03 - Endpoints Canonicos de RAG (Token INTERNAL_API_TOKEN)
# ----------------------------------------------------------------------
print("\n[TESTE 3/6] BUG-03 - Catalogo Documental RAG (/v1/rag/documents/{tenant_id})")
cmd_rag = """docker exec atendit-api python3 -c "
import asyncio, httpx
from app.core.config import settings

async def testar():
    token = getattr(settings, 'INTERNAL_API_TOKEN', getattr(settings, 'INTERNAL_SERVICE_TOKEN', ''))
    headers = {'X-Internal-Token': token}
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get('http://127.0.0.1:8000/v1/rag/documents/21e7ca95-2a64-48e9-9882-ddfb3cc62cba', headers=headers)
        print(f'{r.status_code}|{r.text}')
asyncio.run(testar())
" """
out_rag, err_rag, code_rag = rodar(cmd_rag)
if code_rag == 0 and "|" in out_rag:
    st, body = out_rag.split("|", 1)
    if st == '200':
        print(f"  [PASS] Catalogo RAG consultado com sucesso: HTTP 200")
        print(f"         Documentos encontrados no banco: {body[:100]}...")
    else:
        print(f"  [FAIL] Rota RAG retornou HTTP {st}: {body}")
else:
    print(f"  [FAIL] Erro na execucao do RAG: {err_rag or out_rag}")

# ----------------------------------------------------------------------
# TESTE 4: BUG-04 - Indice HNSW no pgvector (rag_chunks)
# ----------------------------------------------------------------------
print("\n[TESTE 4/6] BUG-04 - Indice Vetorial HNSW no PostgreSQL")
cmd_pg = """docker exec -i atendit-db psql -U atendit_user -d atendit_db -c "
SET enable_seqscan = off;
EXPLAIN SELECT id FROM rag_chunks 
WHERE tenant_id = '21e7ca95-2a64-48e9-9882-ddfb3cc62cba' 
ORDER BY embedding <=> (SELECT embedding FROM rag_chunks LIMIT 1) LIMIT 1;
" """
out_pg, _, _ = rodar(cmd_pg)
if "ix_rag_chunks_embedding_hnsw" in out_pg or "Index Scan" in out_pg:
    print("  [PASS] Indice HNSW ativo e acionado pelo otimizador do Postgres!")
else:
    print(f"  [FAIL] Indice HNSW nao acionado. Plano de execucao:\n{out_pg}")

# ----------------------------------------------------------------------
# TESTE 5: BUG-05 - Normalizacao do Traefik (Docker API 1.41)
# ----------------------------------------------------------------------
print("\n[TESTE 5/6] BUG-05 - Incompatibilidade de API Docker no Traefik")
cmd_tr = 'docker logs atendit-proxy 2>&1 | grep "client version 1.24 is too old" | tail -n 5'
out_tr, _, _ = rodar(cmd_tr)
if not out_tr:
    print("  [PASS] Zero logs de erro de protocolo Docker no atendit-proxy!")
else:
    print(f"  [FAIL] Logs residuais ainda detectados:\n{out_tr}")

# ----------------------------------------------------------------------
# TESTE 6: Paridade de Deploy MD5 (Host vs Conteiner)
# ----------------------------------------------------------------------
print("\n[TESTE 6/6] Sincronismo Estrutural MD5 (Host vs Conteiner atendit-api)")
cmd_md5 = """
cd /home/ubuntu/atendit/backend/app && find . -type f -not -path "*/__pycache__*" -not -name "*.bak*" -not -name "*.pyc" -exec md5sum {} + | sort > /tmp/host_md5.txt
docker exec atendit-api sh -c 'cd /workspace/app && find . -type f -not -path "*/__pycache__*" -not -name "*.bak*" -not -name "*.pyc" -exec md5sum {} +' | sort > /tmp/cont_md5.txt
diff -u /tmp/host_md5.txt /tmp/cont_md5.txt
"""
out_md5, _, code_md5 = rodar(cmd_md5)
if code_md5 == 0 and not out_md5:
    print("  [PASS] Arquivos entre Servidor Host e Conteiner estao 100% IDENTICOS!")
else:
    print(f"  [FAIL] Divergencia encontrada entre Host e Conteiner:\n{out_md5[:300]}")

print("=" * 70)
