import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, Request, HTTPException, status, UploadFile, File
from sqlalchemy import select, delete
from app.core.database import AsyncSessionLocal
from app.models.rag import RAGDocument, RAGChunk
from app.core import autorizacao as _autz
from app.core.config import settings

router = APIRouter(tags=['RAG Management'])

def _validar_uuid(identificador: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(identificador).strip())
    except Exception:
        raise HTTPException(status_code=400, detail='UUID invalido')

async def _verificar_acesso(request: Request, tenant_uuid: uuid.UUID):
    token = request.headers.get('X-Internal-Token') or request.headers.get('x-internal-token')
    token_esperado = getattr(settings, 'INTERNAL_API_TOKEN', getattr(settings, 'INTERNAL_SERVICE_TOKEN', None))
    if token and token_esperado and token == token_esperado:
        return True
    await _autz.exigir_acesso_ao_tenant(request, tenant_id=tenant_uuid)

@router.get('/documents/{tenant_id}', response_model=List[Dict[str, Any]])
async def list_documents(tenant_id: str, request: Request):
    t_uuid = _validar_uuid(tenant_id)
    await _verificar_acesso(request, t_uuid)
    async with AsyncSessionLocal() as session:
        stmt = select(RAGDocument).where(RAGDocument.tenant_id == t_uuid).order_by(RAGDocument.created_at.desc())
        docs = (await session.execute(stmt)).scalars().all()
        return [
            {
                'id': str(d.id),
                'filename': d.filename,
                'file_size': d.file_size,
                'mime_type': d.mime_type,
                'status': d.status,
                'created_at': d.created_at.strftime('%d/%m/%Y %H:%M') if d.created_at else ''
            }
            for d in docs
        ]

@router.delete('/documents/{tenant_id}/{document_id}')
async def delete_document(tenant_id: str, document_id: str, request: Request):
    t_uuid = _validar_uuid(tenant_id)
    d_uuid = _validar_uuid(document_id)
    await _verificar_acesso(request, t_uuid)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(RAGDocument).where(RAGDocument.id == d_uuid, RAGDocument.tenant_id == t_uuid)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            if not doc:
                raise HTTPException(status_code=404, detail='Documento nao encontrado')
            await session.execute(delete(RAGChunk).where(RAGChunk.document_id == d_uuid, RAGChunk.tenant_id == t_uuid))
            await session.execute(delete(RAGDocument).where(RAGDocument.id == d_uuid, RAGDocument.tenant_id == t_uuid))
    return {'status': 'success', 'document_id': str(d_uuid)}

@router.post('/upload/{tenant_id}')
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
    return {'status': 'success', 'data': res}
