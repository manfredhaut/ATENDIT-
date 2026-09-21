"""
Cifra simétrica das credenciais de calendário (Fernet).

Usada por calendar_connections.credentials_encrypted. Perder
CALENDAR_ENCRYPTION_KEY torna o conteúdo irrecuperável e obriga cada
inquilino a refazer o OAuth — ver RESTAURAR-AUTH.md.
"""

import json
import logging
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger("atendit.crypto")


class CifraIndisponivel(RuntimeError):
    """CALENDAR_ENCRYPTION_KEY ausente ou malformada."""


class CifraInvalida(RuntimeError):
    """
    O texto cifrado não abre com a chave atual.

    Quase sempre significa que a chave foi TROCADA depois da gravação — não
    que o dado esteja corrompido. É o modo de falha silencioso descrito em
    RESTAURAR-AUTH.md: o serviço sobe normal e só quebra ao usar a agenda.
    """


def _cifrador() -> Fernet:
    chave = (settings.CALENDAR_ENCRYPTION_KEY or "").strip()
    if not chave:
        raise CifraIndisponivel(
            "CALENDAR_ENCRYPTION_KEY não configurada; credenciais de calendário "
            "não podem ser cifradas nem lidas."
        )
    try:
        return Fernet(chave.encode())
    except Exception as exc:
        raise CifraIndisponivel(f"CALENDAR_ENCRYPTION_KEY inválida: {exc}")


def cifrar_dict(dados: Dict[str, Any]) -> str:
    """Serializa e cifra um dicionário de credenciais."""
    bruto = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _cifrador().encrypt(bruto).decode("ascii")


def decifrar_dict(texto: Optional[str]) -> Dict[str, Any]:
    """Decifra e desserializa. Levanta CifraInvalida se a chave não abrir."""
    if not texto:
        return {}
    try:
        return json.loads(_cifrador().decrypt(texto.encode("ascii")).decode("utf-8"))
    except InvalidToken:
        logger.error(
            "[CRYPTO] Credencial não abre com a CALENDAR_ENCRYPTION_KEY atual. "
            "A chave provavelmente foi trocada — ver RESTAURAR-AUTH.md."
        )
        raise CifraInvalida(
            "Credencial de calendário ilegível com a chave atual. "
            "Reconecte o calendário ou restaure a chave anterior."
        )
