import asyncio
from app.core.database import AsyncSessionLocal
from app.core.feature_flags import is_flag_enabled


async def test_flag_presenthia_core_default_disabled():
    """Valida se a flag presenthia_core permanece desativada por padrao (F0.11)."""
    async with AsyncSessionLocal() as session:
        enabled = await is_flag_enabled("presenthia_core", session)
        assert enabled is False, "A flag presenthia_core DEVE nascer desativada (FALSE)."
        print("✓ [1/2] presenthia_core: desativada por padrao confirmada.")


async def test_flag_inexistente_retorna_false():
    """Valida se uma flag inexistente retorna False com seguranca."""
    async with AsyncSessionLocal() as session:
        enabled = await is_flag_enabled("flag_inexistente_teste_999", session)
        assert enabled is False
        print("✓ [2/2] flag_inexistente: retorno False confirmado.")


async def main():
    print("==================================================")
    print("   VALIDAÇÃO DO MOTOR DE FEATURE FLAGS (F0.11)    ")
    print("==================================================")
    await test_flag_presenthia_core_default_disabled()
    await test_flag_inexistente_retorna_false()
    print("==================================================")
    print(" FEATURE FLAGS VALIDADAS COM SUCESSO!")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
