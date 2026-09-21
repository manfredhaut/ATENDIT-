from app.routes.tenants import router as tenants_router
from app.routes.ai_config import router as ai_config_router
from app.routes.rag import router as rag_router
from app.routes.hardware import router as hardware_router
from app.routes.webhook import router as webhook_router

__all__ = [
    "tenants_router",
    "ai_config_router",
    "rag_router",
    "hardware_router",
    "webhook_router",
]
