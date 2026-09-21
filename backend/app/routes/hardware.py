from fastapi import APIRouter

router = APIRouter(prefix="/hardware", tags=["IoT Addon Module"])

@router.get("/status/{tenant_id}")
def check_iot_addon_licensing(tenant_id: str):
    print(f"[LOG ATENDIT] Verificando barramento de hardware para o tenant: {tenant_id}")
    return {
        "tenant_id": tenant_id,
        "addon_licensed": False,
        "hardware_state": "inert",
        "message": "Módulo de hardware extra inerte. Requer ativação de licença comercial complementar."
    }
