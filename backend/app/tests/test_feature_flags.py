import pytest
from app.core.database import AsyncSessionLocal
from app.core.feature_flags import is_flag_enabled


@pytest.mark.asyncio
async def test_flag_presenthia_core_default_disabled():
    """Valida se a flag presenthia_core permanece desativada por padrao (F0.11)."""
    async with AsyncSessionLocal() as session:
        enabled = await is_flag_enabled("presenthia_core", session)
        assert enabled is False, "A flag presenthia_core DEVE nascer desativada (FALSE)."


@pytest.mark.asyncio
async def test_flag_inexistente_retorna_false():
    """Valida se uma flag inexistente retorna False com seguranca."""
    async with AsyncSessionLocal() as session:
        enabled = await is_flag_enabled("flag_inexistente_teste_999", session)
        assert enabled is False
