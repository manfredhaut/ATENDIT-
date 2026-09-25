import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy import select, desc

from app.core.database import AsyncSessionLocal
from app.models.catalog import Product
from app.models.tenant import Tenant
from app.services.calendar.routes import _exigir_dono

logger = logging.getLogger("atendit.ecommerce")
router = APIRouter(prefix="/v1/ecommerce", tags=["Catálogo & E-commerce Presenthia (F4.1)"])


class ItemCatalogoPayload(BaseModel):
    id: Optional[str] = None
    name: str
    type: str = "product"  # 'product' ou 'service'
    sku: Optional[str] = None
    price_cents: int = 0
    stock_qty: int = 0
    duration_minutes: Optional[int] = 30
    status: str = "active"  # 'active' ou 'draft'
    image_url: Optional[str] = None
    description: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None


@router.get("/items/{tenant}", include_in_schema=False)
async def listar_itens_catalogo(tenant: str, request: Request):
    """Lista todos os produtos e procedimentos de um inquilino autenticado."""
    tenant_id, slug = await _exigir_dono(request, tenant)
    async with AsyncSessionLocal() as session:
        query = (
            select(Product)
            .where(Product.tenant_id == tenant_id)
            .order_by(desc(Product.created_at))
        )
        res = await session.execute(query)
        itens = res.scalars().all()

        return {
            "ok": True,
            "tenant_slug": slug,
            "total": len(itens),
            "items": [
                {
                    "id": str(i.id),
                    "name": i.name,
                    "type": i.type,
                    "sku": i.sku,
                    "price_cents": i.price_cents,
                    "price_formatted": f"R$ {i.price_cents / 100:.2f}".replace(".", ","),
                    "stock_qty": i.stock_qty,
                    "duration_minutes": i.duration_minutes,
                    "status": i.status,
                    "image_url": i.image_url,
                    "description": i.description,
                    "meta_data": i.meta_data,
                    "created_at": i.created_at.isoformat() if i.created_at else None
                }
                for i in itens
            ]
        }


@router.post("/items/{tenant}", include_in_schema=False)
async def salvar_item_catalogo(tenant: str, request: Request, payload: ItemCatalogoPayload):
    """Cria ou atualiza um item no catálogo unificado."""
    tenant_id, slug = await _exigir_dono(request, tenant)

    async with AsyncSessionLocal() as session:
        item = None
        if payload.id:
            try:
                item_uuid = uuid.UUID(payload.id)
                query = select(Product).where(Product.id == item_uuid, Product.tenant_id == tenant_id)
                res = await session.execute(query)
                item = res.scalar_one_or_none()
            except ValueError:
                raise HTTPException(status_code=400, detail="ID de item inválido.")

        if item is None:
            item = Product(
                tenant_id=tenant_id,
                name=payload.name.strip(),
                type=payload.type,
                sku=payload.sku,
                price_cents=payload.price_cents,
                stock_qty=payload.stock_qty,
                duration_minutes=payload.duration_minutes,
                status=payload.status,
                image_url=payload.image_url,
                description=payload.description,
                meta_data=payload.meta_data or {}
            )
            session.add(item)
            logger.info(f"[ECOMMERCE] Novo item criado no catálogo para {slug}: '{payload.name}' ({payload.type})")
        else:
            item.name = payload.name.strip()
            item.type = payload.type
            item.sku = payload.sku
            item.price_cents = payload.price_cents
            item.stock_qty = payload.stock_qty
            item.duration_minutes = payload.duration_minutes
            item.status = payload.status
            item.image_url = payload.image_url
            item.description = payload.description
            if payload.meta_data:
                item.meta_data = payload.meta_data
            logger.info(f"[ECOMMERCE] Item atualizado no catálogo ({slug}): ID {item.id} ('{item.name}')")

        await session.commit()
        await session.refresh(item)

        return {
            "ok": True,
            "id": str(item.id),
            "name": item.name,
            "type": item.type,
            "price_cents": item.price_cents,
            "status": item.status
        }


@router.delete("/items/{tenant}/{item_id}", include_in_schema=False)
async def excluir_item_catalogo(tenant: str, item_id: str, request: Request):
    """Remove um item do catálogo garantindo isolamento por tenant."""
    tenant_id, slug = await _exigir_dono(request, tenant)
    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de item inválido.")

    async with AsyncSessionLocal() as session:
        query = select(Product).where(Product.id == item_uuid, Product.tenant_id == tenant_id)
        res = await session.execute(query)
        item = res.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item não encontrado no catálogo.")

        await session.delete(item)
        await session.commit()
        logger.info(f"[ECOMMERCE] Item {item_id} excluído com sucesso do tenant {slug}.")

        return {"ok": True, "mensagem": "Item removido com sucesso."}


@router.get("/public/items/{tenant_slug}", tags=["Vitrine Pública (F4.2)"])
@router.get("/loja/{tenant_slug}", response_class=HTMLResponse, tags=["Vitrine Pública (F4.2)"], include_in_schema=False)
@router.get("/vitrine/{tenant_slug}", response_class=HTMLResponse, tags=["Vitrine Pública (F4.2)"], include_in_schema=False)
async def renderizar_vitrine_publica(tenant_slug: str):
    """Serve a página pública da vitrine da empresa com identidade Presenthia (F4.2)."""
    vitrine_file = Path(__file__).resolve().parent.parent / "frontend" / "vitrine.html"
    if not vitrine_file.is_file():
        raise HTTPException(status_code=500, detail="Template de vitrine não localizado.")
    return HTMLResponse(vitrine_file.read_text(encoding="utf-8"))


@router.get("/public/items/{tenant_slug}", tags=["Vitrine Pública (F4.2)"])
async def listar_itens_vitrine_publica(tenant_slug: str):
    """Endpoint público de vitrine: retorna apenas itens ativos de um tenant pelo slug."""
    async with AsyncSessionLocal() as session:
        query_tenant = select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_active == True)
        res_tenant = await session.execute(query_tenant)
        t = res_tenant.scalar_one_or_none()
        if not t:
            raise HTTPException(status_code=404, detail="Loja não encontrada ou inativa.")

        query_prods = (
            select(Product)
            .where(Product.tenant_id == t.id, Product.status == "active")
            .order_by(Product.name.asc())
        )
        res_prods = await session.execute(query_prods)
        itens = res_prods.scalars().all()

        meta = t.meta_data or {}
        wa_numero = t.whatsapp_number_e164 or t.whatsapp_notificacoes or meta.get("ai_number") or ""
        msg_boas_vindas = meta.get("mensagem_boas_vindas") or "Produtos e serviços com atendimento inteligente."

        return {
            "tenant_name": t.name,
            "tenant_slug": t.slug,
            "whatsapp_number": wa_numero,
            "welcome_message": msg_boas_vindas,
            "total_items": len(itens),
            "catalog": [
                {
                    "id": str(i.id),
                    "name": i.name,
                    "type": i.type,
                    "price_cents": i.price_cents,
                    "price_formatted": f"R$ {i.price_cents / 100:.2f}".replace(".", ","),
                    "stock_qty": i.stock_qty,
                    "duration_minutes": i.duration_minutes,
                    "image_url": i.image_url,
                    "description": i.description
                }
                for i in itens
            ]
        }
