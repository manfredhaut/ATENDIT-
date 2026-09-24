import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from sqlalchemy import select, delete

from app.services.calendar import calendly_adapter
from app.models.tenant import Tenant
from app.models.scheduling import CalendarConnection, Appointment, ServiceType
from app.core.database import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("calendly_test")


async def suite_calendly_completa():
    print("=" * 70)
    print("      INICIANDO SUÍTE DE TESTES ESTRUTURAIS DO CALENDLY (E2E)        ")
    print("=" * 70)

    test_tenant_id = uuid.uuid4()
    mock_token = "calendly_pat_prod_mock_valid_998877"
    mock_org_uri = "https://api.calendly.com/organizations/ORG_TESTE_ATENDIT"
    mock_user_uri = "https://api.calendly.com/users/USER_TESTE_ATENDIT"
    mock_signing_key = "sec_calendly_signing_key_secret_2026"

    # 1. SETUP: Criar Tenant primeiro e commitar
    agora = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        novo_tenant = Tenant(
            id=test_tenant_id,
            name="Tenant Teste Calendly",
            slug=f"cal-test-{test_tenant_id.hex[:8]}",
            admin_email="teste@calendly.smartinovat.local",
            trial_ends_at=agora + timedelta(days=15),
            is_active=True,
        )
        s.add(novo_tenant)
        await s.commit()
        logger.info(f"Tenant criado com sucesso: {test_tenant_id}")

    # 2. SETUP: Criar ServiceType vinculado ao Tenant
    servico_id = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        servico = ServiceType(
            id=servico_id,
            tenant_id=test_tenant_id,
            name="Consultoria Especializada",
            duration_minutes=45,
            is_active=True,
        )
        s.add(servico)
        await s.commit()
        logger.info(f"ServiceType criado com sucesso: {servico_id}")

    try:
        # Mock das chamadas HTTP externas da API do Calendly
        def mock_chamar(caminho, token, metodo="GET", corpo=None):
            if caminho == "/users/me":
                if token == mock_token:
                    return {
                        "resource": {
                            "current_organization": mock_org_uri,
                            "uri": mock_user_uri,
                            "name": "Responsável Calendly",
                            "email": "atendimento@smartinovat.com",
                            "scheduling_url": "https://calendly.com/smartinovat-atendimento",
                        }
                    }
                else:
                    raise calendly_adapter.TokenInvalido("Token rejeitado pelo Calendly (HTTP 401)")

            if caminho.startswith("/webhook_subscriptions"):
                if metodo == "GET":
                    return {"collection": []}
                if metodo == "POST":
                    return {
                        "resource": {
                            "uri": "https://api.calendly.com/webhook_subscriptions/SUB_CALENDLY_123",
                            "signing_key": mock_signing_key,
                        }
                    }
            return {}

        with patch("app.services.calendar.calendly_adapter._chamar", side_effect=mock_chamar):
            # TESTE 1: Validação de Token (Positivo e Negativo)
            info = await calendly_adapter.validar_token(mock_token)
            assert info["organization"] == mock_org_uri
            assert info["user"] == mock_user_uri
            print("✓ [1/5] validar_token (sucesso): Organização e usuário validados com sucesso.")

            try:
                await calendly_adapter.validar_token("token_invalido_xyz")
                assert False, "Deveria ter disparado TokenInvalido"
            except calendly_adapter.TokenInvalido:
                print("✓ [2/5] validar_token (exceção esperada): TokenInvalido disparado corretamente.")

            # TESTE 2: Conectar e Cifrar
            resultado_conexao = await calendly_adapter.conectar(
                test_tenant_id,
                mock_token,
                scheduling_link="https://calendly.com/smartinovat-atendimento",
            )
            assert resultado_conexao["conectado"] is True
            print("✓ [3/5] conectar: Conexão estabelecida e credenciais cifradas salvas no PostgreSQL.")

            # TESTE 3: Link de Agendamento decifrado para IA
            link_recuperado = await calendly_adapter.link_de_agendamento(test_tenant_id)
            assert link_recuperado == "https://calendly.com/smartinovat-atendimento"
            print(f"   -> link_de_agendamento: URL recuperada com sucesso ({link_recuperado}).")

        # TESTE 4: Verificação Criptográfica de Assinatura HMAC-SHA256
        agora_ts = str(int(time.time()))
        payload_evento = {
            "event": "invitee.created",
            "payload": {
                "uri": "https://api.calendly.com/scheduled_events/EVT_999/invitees/INV_888",
                "name": "Lead Qualificado VIP",
                "email": "lead.qualificado@empresa.com.br",
                "scheduled_event": {
                    "start_time": "2026-10-10T14:00:00Z",
                    "end_time": "2026-10-10T14:45:00Z",
                },
                "questions_and_answers": [
                    {"question": "Telefone / WhatsApp", "answer": "+55 (47) 98888-7777"}
                ],
            },
        }
        corpo_bytes = json.dumps(payload_evento).encode("utf-8")

        # Assinatura HMAC válida
        hmac_calculado = hmac.new(
            mock_signing_key.encode(), f"{agora_ts}.".encode() + corpo_bytes, hashlib.sha256
        ).hexdigest()
        header_valido = f"t={agora_ts},v1={hmac_calculado}"
        assert calendly_adapter.verificar_assinatura(mock_signing_key, header_valido, corpo_bytes) is True

        # Assinatura com replay expirado (> 300s)
        ts_expirado = str(int(time.time()) - 350)
        hmac_expirado = hmac.new(
            mock_signing_key.encode(), f"{ts_expirado}.".encode() + corpo_bytes, hashlib.sha256
        ).hexdigest()
        header_expirado = f"t={ts_expirado},v1={hmac_expirado}"
        assert calendly_adapter.verificar_assinatura(mock_signing_key, header_expirado, corpo_bytes) is False
        print("✓ [4/5] verificar_assinatura: HMAC-SHA256 e proteção contra replay aprovados.")

        # TESTE 5: Ingestão de Webhook, Idempotência e Cancelamento
        res_evento = await calendly_adapter.processar_evento(test_tenant_id, payload_evento)
        assert res_evento["acao"] == "criado"
        appointment_id = uuid.UUID(res_evento["appointment_id"])
        print(f"✓ [5/5] processar_evento (invitee.created): Agendamento {appointment_id} criado no banco.")

        # Idempotência
        res_duplicado = await calendly_adapter.processar_evento(test_tenant_id, payload_evento)
        assert res_duplicado["acao"] == "ignorado"
        print("   -> Idempotência comprovada: reenvio do mesmo evento descartado sem duplicação.")

        # Cancelamento
        payload_evento["event"] = "invitee.canceled"
        res_cancel = await calendly_adapter.processar_evento(test_tenant_id, payload_evento)
        assert res_cancel["acao"] == "cancelado"
        print("   -> Cancelamento comprovado: status atualizado para cancelled.")

        # Auditoria na tabela appointments
        async with AsyncSessionLocal() as s:
            appt = (await s.execute(select(Appointment).where(Appointment.id == appointment_id))).scalar_one()
            assert appt.status == "cancelled"
            assert appt.customer_phone == "5547988887777"
            assert appt.customer_name == "Lead Qualificado VIP"
            assert appt.source == "calendly"
            print(f"   -> Persistência no PostgreSQL: Telefone={appt.customer_phone} | Status={appt.status} | Provedor={appt.provider}")

    finally:
        # Limpeza integral do ambiente de teste
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Appointment).where(Appointment.tenant_id == test_tenant_id))
            await s.execute(delete(CalendarConnection).where(CalendarConnection.tenant_id == test_tenant_id))
            await s.execute(delete(ServiceType).where(ServiceType.tenant_id == test_tenant_id))
            await s.execute(delete(Tenant).where(Tenant.id == test_tenant_id))
            await s.commit()
            logger.info("Limpeza de segurança concluída no banco de dados.")

    print("=" * 70)
    print("  SUÍTE COMPLETA DO CALENDLY EXECUTADA E APROVADA COM SUCESSO!   ")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(suite_calendly_completa())
