#!/usr/bin/env python3
"""v2: mesma logica do patch anterior, mas com o texto EXATO da rota real,
incluindo 3 linhas em branco que tem 4 espacos residuais (nao vazias de
verdade) -- essa diferenca invisivel foi a causa da v1 abortar sem aplicar
nada (com seguranca, como projetado). Idempotente."""
import shutil, sys, py_compile
from datetime import datetime
from pathlib import Path

RAIZ = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/atendit")
ALVO = RAIZ / "backend/app/routes/ecommerce.py"
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")

txt = ALVO.read_text(encoding="utf-8")
if "listar_midias_operacionais" in txt:
    print("Ja aplicado -- nada a fazer.")
    sys.exit(0)

ALVO_IMPORT = "from sqlalchemy import select, desc\n"
NOVO_IMPORT = "from sqlalchemy import select, desc, delete\n"
if ALVO_IMPORT not in txt:
    sys.exit("ERRO: import 'from sqlalchemy import select, desc' nao encontrado.")
txt = txt.replace(ALVO_IMPORT, NOVO_IMPORT, 1)

ALVO_IMPORT2 = "from app.services.calendar.routes import _exigir_dono\n"
NOVO_IMPORT2 = "from app.services.calendar.routes import _exigir_dono\nfrom app.models.media import OperationalMedia\n"
if ALVO_IMPORT2 not in txt:
    sys.exit("ERRO: import '_exigir_dono' nao encontrado.")
txt = txt.replace(ALVO_IMPORT2, NOVO_IMPORT2, 1)

ESP = (" " * 4) + "\n"  # linha "em branco" com 4 espacos residuais, confirmado via cmp -l
ALVO_ROTA = (
    '@router.post("/upload-media/{tenant}", include_in_schema=False)\n'
    'async def upload_midia_item(tenant: str, request: Request, file: UploadFile = File(...)):\n'
    '    tenant_id, slug = await _exigir_dono(request, tenant)\n'
    '    ext = Path(file.filename or "").suffix.lower()\n'
    '    permitidos = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}\n'
    '    if ext not in permitidos:\n'
    '        raise HTTPException(status_code=400, detail="Formato invalido. Envie imagens (JPG, PNG, WebP) ou videos curtos (MP4, WebM).")\n'
    + ESP +
    '    out_dir = Path(__file__).resolve().parent.parent / "static" / "uploads"\n'
    '    out_dir.mkdir(parents=True, exist_ok=True)\n'
    '    fname = f"{slug}_{uuid.uuid4().hex[:10]}{ext}"\n'
    '    dest = out_dir / fname\n'
    + ESP +
    '    content = await file.read()\n'
    '    if len(content) > 15 * 1024 * 1024:\n'
    '        raise HTTPException(status_code=400, detail="Arquivo excede o limite maximo de 15MB.")\n'
    + ESP +
    '    dest.write_bytes(content)\n'
    '    is_vid = ext in {".mp4", ".webm"}\n'
    '    logger.info(f"[ECOMMERCE] Midia enviada para {slug}: {fname} ({len(content)} bytes, video={is_vid})")\n'
    '    return {"ok": True, "url": f"/static/uploads/{fname}", "is_video": is_vid}'
)

NOVO_ROTA = (
    '@router.post("/upload-media/{tenant}", include_in_schema=False)\n'
    'async def upload_midia_item(tenant: str, request: Request, file: UploadFile = File(...)):\n'
    '    tenant_id, slug = await _exigir_dono(request, tenant)\n'
    '    ext = Path(file.filename or "").suffix.lower()\n'
    '    permitidos = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}\n'
    '    if ext not in permitidos:\n'
    '        raise HTTPException(status_code=400, detail="Formato invalido. Envie imagens (JPG, PNG, WebP) ou videos curtos (MP4, WebM).")\n'
    '\n'
    '    out_dir = Path(__file__).resolve().parent.parent / "static" / "uploads"\n'
    '    out_dir.mkdir(parents=True, exist_ok=True)\n'
    '    fname = f"{slug}_{uuid.uuid4().hex[:10]}{ext}"\n'
    '    dest = out_dir / fname\n'
    '\n'
    '    content = await file.read()\n'
    '    if len(content) > 15 * 1024 * 1024:\n'
    '        raise HTTPException(status_code=400, detail="Arquivo excede o limite maximo de 15MB.")\n'
    '\n'
    '    dest.write_bytes(content)\n'
    '    is_vid = ext in {".mp4", ".webm"}\n'
    '    url = f"/static/uploads/{fname}"\n'
    '\n'
    '    async with AsyncSessionLocal() as sessao:\n'
    '        registro = OperationalMedia(\n'
    '            tenant_id=tenant_id,\n'
    '            filename=file.filename or fname,\n'
    '            url=url,\n'
    '            file_type="video" if is_vid else "image",\n'
    '            file_size=len(content),\n'
    '        )\n'
    '        sessao.add(registro)\n'
    '        await sessao.commit()\n'
    '        await sessao.refresh(registro)\n'
    '\n'
    '    logger.info(f"[ECOMMERCE] Midia enviada para {slug}: {fname} ({len(content)} bytes, video={is_vid})")\n'
    '    return {"ok": True, "id": str(registro.id), "url": url, "is_video": is_vid, "filename": file.filename or fname}\n'
    '\n'
    '\n'
    '@router.get("/media/{tenant}", include_in_schema=False)\n'
    'async def listar_midias_operacionais(tenant: str, request: Request):\n'
    '    tenant_id, slug = await _exigir_dono(request, tenant)\n'
    '    async with AsyncSessionLocal() as sessao:\n'
    '        res = await sessao.execute(\n'
    '            select(OperationalMedia)\n'
    '            .where(OperationalMedia.tenant_id == tenant_id)\n'
    '            .order_by(desc(OperationalMedia.created_at))\n'
    '        )\n'
    '        itens = res.scalars().all()\n'
    '        return [\n'
    '            {\n'
    '                "id": str(i.id),\n'
    '                "filename": i.filename,\n'
    '                "url": i.url,\n'
    '                "file_type": i.file_type,\n'
    '                "file_size": i.file_size,\n'
    '                "created_at": i.created_at.isoformat() if i.created_at else None,\n'
    '            }\n'
    '            for i in itens\n'
    '        ]\n'
    '\n'
    '\n'
    '@router.delete("/media/{tenant}/{media_id}", include_in_schema=False)\n'
    'async def excluir_midia_operacional(tenant: str, media_id: str, request: Request):\n'
    '    tenant_id, slug = await _exigir_dono(request, tenant)\n'
    '    try:\n'
    '        m_uuid = uuid.UUID(media_id)\n'
    '    except ValueError:\n'
    '        raise HTTPException(status_code=400, detail="ID invalido.")\n'
    '\n'
    '    async with AsyncSessionLocal() as sessao:\n'
    '        res = await sessao.execute(\n'
    '            select(OperationalMedia).where(OperationalMedia.id == m_uuid, OperationalMedia.tenant_id == tenant_id)\n'
    '        )\n'
    '        registro = res.scalar_one_or_none()\n'
    '        if not registro:\n'
    '            raise HTTPException(status_code=404, detail="Midia nao encontrada.")\n'
    '\n'
    '        try:\n'
    '            caminho_arquivo = Path(__file__).resolve().parent.parent / "static" / "uploads" / Path(registro.url).name\n'
    '            caminho_arquivo.unlink(missing_ok=True)\n'
    '        except Exception as exc:\n'
    '            logger.warning(f"[ECOMMERCE] Falha ao remover arquivo fisico de midia {media_id}: {exc}")\n'
    '\n'
    '        await sessao.execute(delete(OperationalMedia).where(OperationalMedia.id == m_uuid))\n'
    '        await sessao.commit()\n'
    '\n'
    '    return {"ok": True}'
)

if ALVO_ROTA not in txt:
    sys.exit("ERRO: bloco original da rota upload-media nao encontrado, mesmo com whitespace corrigido.")
txt = txt.replace(ALVO_ROTA, NOVO_ROTA, 1)

bak = RAIZ / "_bak"; bak.mkdir(exist_ok=True)
shutil.copy2(ALVO, bak / f"ecommerce.py.{CARIMBO}")
ALVO.write_text(txt, encoding="utf-8")
py_compile.compile(str(ALVO), doraise=True)
print(f"Aplicado. Backup em _bak/ecommerce.py.{CARIMBO}")
print("Sintaxe valida.")
