#!/usr/bin/env python3
"""rag.py: POST /upload/{tenant_id} chamava rag_service.ingerir_documento(),
que nao existe -- AttributeError, HTTP 500 em qualquer upload (confirmado
em producao: .docx real, log com o traceback completo). O metodo real e
indexar_documento(db, tenant_id, caminho: Path, filename, tamanho_bytes,
mime_type) -- assinatura totalmente diferente, exige sessao de banco e um
ARQUIVO EM DISCO (extrair_texto le de Path), nao bytes em memoria.

Fix: salva o upload no volume persistente RAG_STORAGE_PATH/{tenant_id}/,
abre uma sessao real, chama indexar_documento com a assinatura correta.
Idempotente."""
import shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/routes/rag.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "indexar_documento(" in txt and "rag_service.ingerir_documento" not in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ALVO_BLOCO = '''@router.post('/upload/{tenant_id}')
async def upload_document(tenant_id: str, request: Request, file: UploadFile = File(...)):
    t_uuid = _validar_uuid(tenant_id)
    await _verificar_acesso(request, t_uuid)
    from app.services.rag_service import rag_service
    conteudo = await file.read()
    res = await rag_service.ingerir_documento(
        tenant_id=t_uuid,
        nome_arquivo=file.filename,
        conteudo_bytes=conteudo,
        mime_type=file.content_type or 'application/octet-stream'
    )
    return {'status': 'success', 'data': res}'''

NOVO_BLOCO = '''@router.post('/upload/{tenant_id}')
async def upload_document(tenant_id: str, request: Request, file: UploadFile = File(...)):
    t_uuid = _validar_uuid(tenant_id)
    await _verificar_acesso(request, t_uuid)
    from pathlib import Path as _Path
    from app.services.rag_service import rag_service

    conteudo = await file.read()

    pasta_tenant = _Path(settings.RAG_STORAGE_PATH) / str(t_uuid)
    pasta_tenant.mkdir(parents=True, exist_ok=True)
    extensao = _Path(file.filename or "").suffix.lower()
    caminho_destino = pasta_tenant / f"{uuid.uuid4().hex}{extensao}"
    caminho_destino.write_bytes(conteudo)

    async with AsyncSessionLocal() as sessao:
        res = await rag_service.indexar_documento(
            db=sessao,
            tenant_id=t_uuid,
            caminho=caminho_destino,
            filename=file.filename,
            tamanho_bytes=len(conteudo),
            mime_type=file.content_type or 'application/octet-stream',
        )
    return {'status': 'success', 'data': res}'''

if ALVO_BLOCO not in txt:
    sys.exit("ERRO: bloco original da rota de upload nao encontrado no formato esperado.")

novo_txt = txt.replace(ALVO_BLOCO, NOVO_BLOCO, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"rag.py.{CARIMBO}")
ALVO.write_text(novo_txt, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/rag.py.{CARIMBO}")
print("Sintaxe valida.")
