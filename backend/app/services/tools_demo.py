"""
Ferramenta de demonstração usada para validar a INFRAESTRUTURA de function
calling do llm_service.

Não está ligada ao webhook e não é oferecida a nenhum cliente: o webhook
continua chamando generate_response() sem o parâmetro 'tools'. Este módulo
existe para que o teste da infraestrutura seja reproduzível depois, em vez de
viver num script solto que some.

Quando houver funções reais (consultar horário livre, criar compromisso), elas
seguem exatamente este formato: uma declaração em JSON Schema + um handler.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List

FUSO_PADRAO = "America/Sao_Paulo"


def get_current_time(timezone: str = FUSO_PADRAO) -> Dict[str, Any]:
    """Devolve a data e a hora correntes no fuso informado."""
    try:
        agora = datetime.now(ZoneInfo(timezone))
    except Exception:
        # Fuso desconhecido não derruba a chamada: cai no padrão e avisa o modelo.
        agora = datetime.now(ZoneInfo(FUSO_PADRAO))
        return {
            "datetime_iso": agora.isoformat(),
            "timezone": FUSO_PADRAO,
            "aviso": f"Fuso '{timezone}' desconhecido; usado {FUSO_PADRAO}.",
        }

    return {
        "datetime_iso": agora.isoformat(),
        "timezone": timezone,
        "data_formatada": agora.strftime("%d/%m/%Y"),
        "hora_formatada": agora.strftime("%H:%M"),
    }


# Declaração no formato que o Gemini consome (JSON Schema).
DECLARACAO_GET_CURRENT_TIME: Dict[str, Any] = {
    "name": "get_current_time",
    "description": (
        "Retorna a data e a hora atuais. Use sempre que o usuário perguntar as "
        "horas, a data de hoje, ou precisar de referência temporal para responder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "Fuso horário IANA, por exemplo 'America/Sao_Paulo'. "
                    "Se omitido, usa o horário de Brasília."
                ),
            }
        },
        "required": [],
    },
}

FERRAMENTAS_DEMO: List[Dict[str, Any]] = [DECLARACAO_GET_CURRENT_TIME]

HANDLERS_DEMO: Dict[str, Callable] = {
    "get_current_time": get_current_time,
}
