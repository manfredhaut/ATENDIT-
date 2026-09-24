from functools import lru_cache
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações centrais da aplicação carregadas e validadas a partir do ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True
    )

    # Identificação do Serviço
    APP_NAME: str = "ATENDIT SaaS API Engine"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    CORS_ORIGINS: str = "*"

    # Conexão com Banco de Dados PostgreSQL (pgvector)
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://atendit_user:SenhaForteSegura_2026_@postgres:5432/atendit_db"
    )

    # Redis Cache / Filas Celery
    REDIS_URL: str = "redis://redis:6379/0"

    # Integração com LLM (Google Gemini)
    GEMINI_API_KEY: Optional[str] = Field(default=None, validation_alias="GEMINI_DEFAULT_KEY")
    # Medido em 2026-08-30: projetos novos do Google Cloud recebem 404
    # em gemini-1.5-flash e gemini-2.5-flash. Este e o padrao verificado.
    DEFAULT_GEMINI_MODEL: str = "gemini-3.5-flash"
    # Cadeia de reserva, tentada em ordem se o modelo pedido der 404.
    GEMINI_MODELOS_FALLBACK: str = "gemini-2.5-flash,gemini-flash-latest,gemini-3.5-flash"

    # Gateway WhatsApp (Evolution API)
    EVOLUTION_API_URL: str = "http://evolution-api:8080"
    EVOLUTION_API_KEY: Optional[str] = None
    EVOLUTION_HOST: Optional[str] = None

    # Avaliador Semântico JEV (TypeSafe AI) & Escalamento de Vídeo
    JEV_API_KEY: Optional[str] = Field(default=None, validation_alias="JEV_API_KEY")
    JEV_API_URL: str = Field(default="https://api.typesafe.ai/v1/evaluate", validation_alias="JEV_API_URL")
    JEV_VIDEO_THRESHOLD: float = Field(default=0.75, validation_alias="JEV_VIDEO_THRESHOLD")

    # Parâmetros de Negócio SaaS e Storage RAG
    DEFAULT_TRIAL_DAYS: int = 15
    RAG_STORAGE_PATH: str = "/workspace/storage/tenants"
    VECTOR_DIMENSION: int = 768

    # Autenticacao maquina-a-maquina das rotas administrativas.
    INTERNAL_API_TOKEN: Optional[str] = None
    # Segredo compartilhado com o gateway Evolution. Ele o devolve no header
    # X-Webhook-Secret a cada chamada de webhook (campo 'headers' do
    # webhook/set). Sem valor configurado a rota FECHA, nao abre.
    WEBHOOK_SECRET: Optional[str] = None
    # E-mail transacional (Resend). Sem chave, enviar_email() recusa e
    # registra ERROR -- nunca finge que enviou.
    RESEND_API_KEY: Optional[str] = None
    EMAIL_REMETENTE: str = "ATENDIT <nao-responda@smartinovat.com>"
    URL_PUBLICA: str = "https://atendit.smartinovat.com"
    # Chave Fernet das credenciais de calendario (ver RESTAURAR-AUTH.md).
    CALENDAR_ENCRYPTION_KEY: Optional[str] = None

    # OAuth do Google Calendar (projeto 612689782326).
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = None
    GOOGLE_OAUTH_CLIENT_SECRET: Optional[str] = None
    MICROSOFT_OAUTH_CLIENT_ID: Optional[str] = None
    MICROSOFT_OAUTH_CLIENT_SECRET: Optional[str] = None
    # Base publica usada para montar o redirect_uri do OAuth. Precisa bater
    # EXATAMENTE com o que esta registrado no Google Cloud Console.
    PUBLIC_BASE_URL: str = "https://atendit.smartinovat.com"

    # Autenticacao do painel (PARTE 5).
    ADMIN_PASSWORD: Optional[str] = None
    PANEL_SESSION_SECRET: Optional[str] = None

    # Para onde vai o aviso de novo lead da landing. Configuravel para nao
    # exigir deploy quando o responsavel mudar.
    ADMIN_NOTIFICATION_PHONE: str = "5585989020465"
    ADMIN_NOTIFICATION_INSTANCE: str = "atendit_manitest"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def ensure_async_driver(cls, v: str) -> str:
        """Garante que a string de conexão utilize o driver assíncrono asyncpg."""
        if not v:
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql+psycopg://"):
            return v.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        return v

    @property
    def modelos_fallback(self) -> List[str]:
        """Cadeia de modelos de reserva, na ordem de tentativa."""
        return [m.strip() for m in self.GEMINI_MODELOS_FALLBACK.split(",") if m.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
