import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("atendit.evolution_service")


class EvolutionService:
    """Cliente assíncrono para despacho de mensagens e comandos à Evolution API."""

    def __init__(self):
        self.base_url = settings.EVOLUTION_API_URL.rstrip("/")
        self.default_api_key = settings.EVOLUTION_API_KEY

    async def send_text_message(
        self,
        instance_name: str,
        recipient_number: str,
        text: str,
        custom_api_key: Optional[str] = None,
    ) -> bool:
        """
        Envia mensagem de texto para um número de WhatsApp via Evolution API.
        Endpoint: POST /message/sendText/{instance_name}
        """
        api_key = custom_api_key or self.default_api_key
        url = f"{self.base_url}/message/sendText/{instance_name}"

        headers = {
            "Content-Type": "application/json",
            "apikey": api_key or "",
        }

        payload = {
            "number": recipient_number,
            "options": {
                "delay": 1200,
                "presence": "composing",
                "linkPreview": True,
            },
            "text": text,
        }

        logger.info(
            f"[EVOLUTION DISPATCH] Enviando mensagem via instância '{instance_name}' "
            f"para o destinatário '{recipient_number}' ({len(text)} caracteres)"
        )

        # 45s, nao 15s. Medido em 2026-09-01: no caminho quente o gateway
        # responde em 0,9-5,3s, mas o PRIMEIRO envio depois de um restart leva
        # ~19s (carga da sessao Baileys a frio). Com 15s esse envio estourava o
        # timeout DEPOIS de a mensagem ja ter saido -- o log dizia
        # "enviado=False" para uma mensagem entregue. Log que mente sobre
        # entrega e pior que envio lento: convida a reenviar e duplicar.
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info(
                        f"[EVOLUTION SUCCESS] Mensagem entregue com sucesso à Evolution API para '{recipient_number}'."
                    )
                    return True
                else:
                    logger.error(
                        f"[EVOLUTION ERROR] Falha no envio (HTTP {response.status_code}): {response.text}"
                    )
                    return False
            except httpx.RequestError as exc:
                logger.error(
                    f"[EVOLUTION NETWORK ERROR] Erro de conexão com o Gateway Evolution ({url}): {str(exc)}"
                )
                return False


evolution_service = EvolutionService()
