import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base

class TenantMetaConfig(Base):
    __tablename__ = "tenant_meta_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    app_id = Column(String(100), nullable=True)
    waba_id = Column(String(100), nullable=True)
    phone_number_id = Column(String(100), nullable=True)
    access_token = Column(Text, nullable=True)
    verify_token = Column(String(255), nullable=False)
    business_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
