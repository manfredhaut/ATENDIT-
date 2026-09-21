"""
Ferramentas de agendamento expostas ao Gemini (function calling).

Ligam o modelo ao scheduling_service. Não falam com o Google diretamente:
o webhook não deve saber qual provedor está conectado.

--------------------------------------------------------------------------
POR QUE OS HANDLERS SÃO CLOSURES
--------------------------------------------------------------------------
tenant_id e telefone do cliente vêm do CONTEXTO da conversa, não do modelo.
Se fossem parâmetros da função, o Gemini poderia inventá-los — e cancelar o
compromisso de outra pessoa seria uma alucinação com consequência real.
Ficam presos no fechamento; o modelo só informa o que legitimamente sabe:
o horário, o serviço e o nome.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.services import scheduling_service as sched

logger = logging.getLogger("atendit.scheduling_tools")

FUSO = ZoneInfo("America/Sao_Paulo")

# Faixas horarias por periodo do dia. manha=08-12 conforme definido no
# produto. Nao muda o resultado de check_availability porque business_hours
# comeca as 09:00 -- so alinha o vocabulario entre as duas ferramentas.
PERIODOS = {
    "manha": (8, 12),
    "manhã": (8, 12),
    "tarde": (12, 18),
    "noite": (18, 22),
    "qualquer": (0, 24),
}


def _parse_data(texto: str) -> datetime:
    """Aceita YYYY-MM-DD ou ISO-8601 completo; devolve com fuso de Brasília."""
    t = (texto or "").strip()
    if not t:
        raise ValueError("Data não informada.")
    dt = datetime.fromisoformat(t)
    return dt.replace(tzinfo=FUSO) if dt.tzinfo is None else dt


# --------------------------------------------------------------------------
# Declarações (JSON Schema) — o que o modelo enxerga
# --------------------------------------------------------------------------
DECLARACOES: List[Dict[str, Any]] = [
    {
        "name": "check_availability",
        "description": (
            "Consulta os horários REALMENTE livres na agenda para uma data. "
            "Use SEMPRE antes de propor qualquer horário ao cliente — nunca invente horários."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "data_desejada": {"type": "string", "description": "Data no formato AAAA-MM-DD."},
                "periodo": {
                    "type": "string",
                    "description": "Parte do dia: 'manha', 'tarde', 'noite' ou 'qualquer'.",
                },
                "service_type": {"type": "string", "description": "Nome do serviço, se o cliente citou algum."},
            },
            "required": ["data_desejada"],
        },
    },
    {
        "name": "book_appointment",
        "description": (
            "Marca o compromisso num horário específico. Só chame depois de o cliente "
            "escolher um horário que veio de check_availability."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "datetime_desejado": {
                    "type": "string",
                    "description": "Início do compromisso em AAAA-MM-DDTHH:MM (horário de Brasília).",
                },
                "nome_cliente": {"type": "string", "description": "Nome do cliente, se ele informou."},
                "service_type": {"type": "string", "description": "Nome do serviço, se houver mais de um."},
            },
            "required": ["datetime_desejado"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": (
            "Move um compromisso existente do cliente para outro horário. Use quando ele "
            "pedir para REMARCAR, TROCAR, ADIAR ou ANTECIPAR um horário já marcado — não "
            "cancele e marque de novo separadamente, use esta função. Confirme antes que o "
            "horário novo aparece em check_availability."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "datetime_novo": {
                    "type": "string",
                    "description": "Novo início desejado, em AAAA-MM-DDTHH:MM (horário de Brasília).",
                },
                "datetime_atual": {
                    "type": "string",
                    "description": (
                        "Início do compromisso que será movido (AAAA-MM-DDTHH:MM). "
                        "Omita para mover o próximo compromisso do cliente."
                    ),
                },
            },
            "required": ["datetime_novo"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancela um compromisso já marcado deste cliente.",
        "parameters": {
            "type": "object",
            "properties": {
                "datetime_desejado": {
                    "type": "string",
                    "description": "Início do compromisso a cancelar (AAAA-MM-DDTHH:MM). Omita para cancelar o próximo.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "share_scheduling_link",
        "description": (
            "Devolve o link público de agendamento do negócio (Calendly). Use quando o "
            "cliente perguntar COMO agendar, pedir o link, ou quando o agendamento por "
            "aqui não estiver disponível. Se não houver link configurado, diga que o "
            "agendamento é feito por aqui mesmo e ofereça consultar horários."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_my_appointments",
        "description": "Lista os compromissos futuros já marcados para este cliente.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "join_waitlist",
        "description": (
            "Coloca o cliente na lista de espera para uma janela de datas, quando não há "
            "horário livre no período que ele queria."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "data_inicio_desejada": {"type": "string", "description": "Início da janela (AAAA-MM-DD ou ISO)."},
                "data_fim_desejada": {"type": "string", "description": "Fim da janela (AAAA-MM-DD ou ISO)."},
                "periodo": {
                    "type": "string",
                    "description": (
                        "Parte do dia desejada: 'manha', 'tarde', 'noite' ou 'qualquer'. "
                        "Informe sempre que o cliente disser algo como 'de manhã' — sem isso "
                        "a fila registra o dia inteiro e ele é avisado de horários que não quer."
                    ),
                },
                "nome_cliente": {"type": "string", "description": "Nome do cliente, se ele informou."},
                "service_type": {"type": "string", "description": "Nome do serviço, se houver mais de um."},
            },
            "required": ["data_inicio_desejada", "data_fim_desejada"],
        },
    },
]


# --------------------------------------------------------------------------
# Fábrica de handlers
# --------------------------------------------------------------------------
def construir_ferramentas(
    tenant_id: uuid.UUID, customer_phone: str, customer_name: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Callable]]:
    """Devolve (declaracoes, handlers) com o contexto do atendimento preso."""

    async def _servico_id(nome: Optional[str]) -> Optional[uuid.UUID]:
        if not nome:
            return None
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            try:
                servico = await sched._carregar_servico(s, tenant_id, nome=nome)
                return servico.id
            except sched.ServicoNaoEncontrado:
                return None

    async def check_availability(
        data_desejada: str, periodo: str = "qualquer", service_type: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            dia = _parse_data(data_desejada)
        except ValueError as exc:
            return {"erro": f"Data inválida: {exc}"}

        h_ini, h_fim = PERIODOS.get((periodo or "qualquer").lower(), (0, 24))
        inicio = dia.replace(hour=h_ini, minute=0, second=0, microsecond=0)
        fim = dia.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1 if h_fim >= 24 else 0, hours=0 if h_fim >= 24 else h_fim
        )

        try:
            r = await sched.find_available_slots(
                tenant_id=tenant_id,
                service_type_id=await _servico_id(service_type),
                date_range=(inicio, fim),
            )
        except sched.ServicoNaoEncontrado as exc:
            return {"erro": str(exc)}
        except Exception as exc:
            logger.error(f"[TOOL check_availability] {exc}")
            return {"erro": "Não consegui consultar a agenda agora."}

        return {
            "servico": r["servico"],
            "duracao_minutos": r["duracao_minutos"],
            "fonte": r["fonte"],
            "total_livres": r["total"],
            "horarios": [s["rotulo"] for s in r["slots"]],
            "horarios_iso": [s["inicio"] for s in r["slots"]],
        }

    async def book_appointment(
        datetime_desejado: str, nome_cliente: Optional[str] = None, service_type: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            quando = _parse_data(datetime_desejado)
        except ValueError as exc:
            return {"erro": f"Horário inválido: {exc}"}
        try:
            return await sched.book_appointment(
                tenant_id=tenant_id,
                service_type_id=await _servico_id(service_type),
                inicio=quando,
                customer_phone=customer_phone,
                customer_name=nome_cliente or customer_name,
            )
        except sched.HorarioIndisponivel as exc:
            return {"erro": str(exc), "disponivel": False}
        except Exception as exc:
            logger.error(f"[TOOL book_appointment] {exc}")
            return {"erro": "Não consegui concluir a marcação agora."}

    async def cancel_appointment(datetime_desejado: Optional[str] = None) -> Dict[str, Any]:
        quando = None
        if datetime_desejado:
            try:
                quando = _parse_data(datetime_desejado)
            except ValueError as exc:
                return {"erro": f"Horário inválido: {exc}"}
        try:
            return await sched.cancel_appointment(
                tenant_id=tenant_id, customer_phone=customer_phone, inicio=quando
            )
        except sched.CompromissoNaoEncontrado as exc:
            return {"erro": str(exc)}
        except Exception as exc:
            logger.error(f"[TOOL cancel_appointment] {exc}")
            return {"erro": "Não consegui cancelar agora."}

    async def reschedule_appointment(
        datetime_novo: str, datetime_atual: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            novo = _parse_data(datetime_novo)
            atual = _parse_data(datetime_atual) if datetime_atual else None
        except ValueError as exc:
            return {"erro": f"Horário inválido: {exc}"}
        try:
            return await sched.reschedule_appointment(
                tenant_id=tenant_id,
                customer_phone=customer_phone,
                datetime_atual=atual,
                datetime_novo=novo,
            )
        except sched.CompromissoNaoEncontrado as exc:
            return {"erro": str(exc), "remarcado": False}
        except sched.HorarioIndisponivel as exc:
            # O compromisso ORIGINAL continua de pe -- e isso que o modelo
            # precisa dizer ao cliente, para ele nao achar que perdeu a vaga.
            return {
                "erro": str(exc),
                "remarcado": False,
                "observacao": "O horário original segue confirmado; nada foi perdido.",
            }
        except Exception as exc:
            logger.error(f"[TOOL reschedule_appointment] {exc}")
            return {"erro": "Não consegui remarcar agora.", "remarcado": False}

    async def share_scheduling_link() -> Dict[str, Any]:
        try:
            from app.services.calendar import calendly_adapter
            link = await calendly_adapter.link_de_agendamento(tenant_id)
        except Exception as exc:
            logger.error(f"[TOOL share_scheduling_link] {exc}")
            return {"tem_link": False, "erro": "Não consegui buscar o link agora."}
        if not link:
            return {
                "tem_link": False,
                "observacao": "Não há link externo configurado; o agendamento é feito por esta conversa.",
            }
        return {"tem_link": True, "scheduling_link": link}

    async def list_my_appointments() -> Dict[str, Any]:
        try:
            itens = await sched.list_appointments(tenant_id=tenant_id, customer_phone=customer_phone)
            return {"total": len(itens), "compromissos": itens}
        except Exception as exc:
            logger.error(f"[TOOL list_my_appointments] {exc}")
            return {"erro": "Não consegui consultar seus compromissos agora."}

    async def join_waitlist(
        data_inicio_desejada: str,
        data_fim_desejada: str,
        periodo: Optional[str] = None,
        nome_cliente: Optional[str] = None,
        service_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            ini = _parse_data(data_inicio_desejada)
            fim = _parse_data(data_fim_desejada)

            faixa = PERIODOS.get((periodo or "").strip().lower())
            if faixa:
                # Estreita a janela para as horas do periodo pedido. Sem isso,
                # quem pede "de manha" era notificado de vaga as 17h.
                h_ini, h_fim = faixa
                ini = ini.replace(hour=h_ini, minute=0, second=0, microsecond=0)
                if h_fim >= 24:
                    fim = fim.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - timedelta(minutes=1)
                else:
                    fim = fim.replace(hour=h_fim, minute=0, second=0, microsecond=0)
            elif fim.hour == 0 and fim.minute == 0:
                # Só data, sem hora e sem periodo: dia inteiro, senão a janela
                # teria duração zero e o CHECK do banco recusaria.
                fim = fim + timedelta(days=1) - timedelta(minutes=1)
        except ValueError as exc:
            return {"erro": f"Data inválida: {exc}"}
        try:
            return await sched.join_waitlist(
                tenant_id=tenant_id,
                service_type_id=await _servico_id(service_type),
                customer_phone=customer_phone,
                desired_start=ini,
                desired_end=fim,
                customer_name=nome_cliente or customer_name,
            )
        except Exception as exc:
            logger.error(f"[TOOL join_waitlist] {exc}")
            return {"erro": "Não consegui te colocar na lista de espera agora."}

    handlers: Dict[str, Callable] = {
        "check_availability": check_availability,
        "book_appointment": book_appointment,
        "cancel_appointment": cancel_appointment,
        "reschedule_appointment": reschedule_appointment,
        "list_my_appointments": list_my_appointments,
        "share_scheduling_link": share_scheduling_link,
        "join_waitlist": join_waitlist,
    }
    return DECLARACOES, handlers


def instrucao_de_contexto() -> str:
    """
    Data corrente injetada no system prompt.

    Sem isso o modelo não resolve "amanhã" — ele não sabe que dia é hoje, e
    chutaria uma data, fazendo check_availability consultar o dia errado.
    """
    agora = datetime.now(FUSO)
    return (
        f"\n\nCONTEXTO TEMPORAL: hoje é {agora.strftime('%A, %d/%m/%Y')} e agora são "
        f"{agora.strftime('%H:%M')} (horário de Brasília). Use isto para resolver "
        f"expressões como 'amanhã', 'depois de amanhã' e 'semana que vem'.\n"
        "FERRAMENTAS DE AGENDA: você pode consultar horários livres e marcar, cancelar "
        "ou listar compromissos usando as ferramentas disponíveis. NUNCA invente horários "
        "nem confirme uma marcação sem antes chamar a ferramenta correspondente."
    )
