import logging
import uuid
from typing import Optional, Callable
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.feature_flag import FeatureFlag

logger = logging.getLogger("atendit.feature_flags")


async def is_flag_enabled(
    flag_key: str,
    db: AsyncSession,
    tenant_id: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> bool:
    try:
        if tenant_id:
            try:
                t_uuid = uuid.UUID(str(tenant_id))
                query_tenant = select(FeatureFlag).where(
                    FeatureFlag.key == flag_key,
                    FeatureFlag.tenant_id == t_uuid,
                )
                res_tenant = await db.execute(query_tenant)
                flag_tenant = res_tenant.scalar_one_or_none()
                if flag_tenant is not None:
                    return flag_tenant.enabled
            except ValueError:
                pass

        if plan_id:
            query_plan = select(FeatureFlag).where(
                FeatureFlag.key == flag_key,
                FeatureFlag.plan_id == str(plan_id),
                FeatureFlag.scope == "plan",
            )
            res_plan = await db.execute(query_plan)
            flag_plan = res_plan.scalar_one_or_none()
            if flag_plan is not None:
                return flag_plan.enabled

        query_global = select(FeatureFlag).where(
            FeatureFlag.key == flag_key,
            FeatureFlag.scope == "global",
        )
        res_global = await db.execute(query_global)
        flag_global = res_global.scalar_one_or_none()
        if flag_global is not None:
            return flag_global.enabled

        return False
    except Exception as exc:
        logger.error(f"[FEATURE_FLAG ERROR] Erro ao consultar flag '{flag_key}': {exc}")
        return False


def flag_on(flag_key: str) -> Callable:
    async def _dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> bool:
        tenant_id = (
            request.headers.get("X-Tenant-ID")
            or getattr(request.state, "tenant_id", None)
            or request.query_params.get("tenant_id")
        )
        plan_id = getattr(request.state, "plan_id", None)

        enabled = await is_flag_enabled(flag_key, db, tenant_id=tenant_id, plan_id=plan_id)
        if not enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not Found",
            )
        return True

    return _dependency
