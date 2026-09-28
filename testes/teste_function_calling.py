"""
TESTE 3 - Infraestrutura de function calling no llm_service.

Prova o ciclo completo:
    modelo pede funcao -> backend executa -> devolve resultado -> modelo
    formula a resposta final ao cliente.

Usa a funcao dummy get_current_time (app/services/tools_demo.py). Nao ha
nenhuma funcao de negocio nesta fase - o objeto sob teste e a INFRAESTRUTURA.

As asserções sao ancoradas em FATOS VERIFICAVEIS, nunca na redacao da resposta:
  - o handler foi de fato invocado (contador proprio, nao inferencia)
  - a hora que o handler devolveu aparece no texto final
A IA e nao-deterministica; casar string de redacao produziria teste instavel.
"""

import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services.llm_service import llm_service
from app.services.tools_demo import DECLARACAO_GET_CURRENT_TIME, get_current_time

FALHAS = []
INVOCACOES = []


def checar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    if condicao:
        print(f"  [OK]     {descricao}")
    else:
        print(f"  [FALHOU] {descricao} {detalhe}")
        FALHAS.append(descricao)


def handler_instrumentado(timezone: str = "America/Sao_Paulo") -> dict:
    """Envolve o handler real para registrar que ele foi mesmo chamado."""
    INVOCACOES.append({"timezone": timezone})
    resultado = get_current_time(timezone=timezone)
    print(f"    -> handler get_current_time EXECUTADO: {resultado}")
    return resultado


async def obter_chave() -> str:
    """
    Mesma precedencia do webhook: chave do inquilino antes da do ambiente.
    A do ambiente esta truncada (31 chars) e o Google a rejeita com 400.
    """
    async with AsyncSessionLocal() as sessao:
        chave = (await sessao.execute(text("SELECT api_key FROM ai_configs LIMIT 1"))).scalar_one_or_none()
    print(f"Chave usada: do inquilino (len={len(chave) if chave else 0})")
    return chave


async def principal() -> None:
    chave = await obter_chave()

    # ---------- CASO A: pergunta que EXIGE a ferramenta ----------
    print("\n[CASO A] Pergunta que exige a ferramenta: 'Que horas sao agora?'")
    resposta = await llm_service.generate_response(
        system_prompt="Voce e um assistente objetivo. Use as ferramentas disponiveis quando precisar.",
        user_message="Que horas sao agora, no horario de Brasilia?",
        tools=[DECLARACAO_GET_CURRENT_TIME],
        tool_handlers={"get_current_time": handler_instrumentado},
        temperature=0.0,
        custom_api_key=chave,
    )
    print(f"\n  RESPOSTA FINAL: {resposta}")

    checar(len(INVOCACOES) >= 1, "o Gemini chamou a funcao get_current_time",
           f"(invocacoes: {len(INVOCACOES)})")
    checar(bool(resposta and resposta.strip()), "resposta final nao veio vazia")

    # A hora que o backend devolveu tem de aparecer no texto final: e isso que
    # prova que o modelo USOU o resultado, e nao que inventou um horario.
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    hh = agora.strftime("%H")
    hh_12 = agora.strftime("%I").lstrip("0")
    checar(
        hh in resposta or hh_12 in resposta,
        "a hora devolvida pelo handler aparece na resposta final",
        f"(esperado conter '{hh}' ou '{hh_12}'; resposta: '{resposta[:80]}')",
    )

    print("  (pausa de 20s: free tier limita a 5 requisicoes/minuto)")
    await asyncio.sleep(20)

    # ---------- CASO B: pergunta que NAO precisa de ferramenta ----------
    print("\n[CASO B] Pergunta que NAO precisa de ferramenta (regressao)")
    antes = len(INVOCACOES)
    resposta_b = await llm_service.generate_response(
        system_prompt="Voce e um assistente objetivo.",
        user_message="Responda apenas com a palavra: funcionando",
        tools=[DECLARACAO_GET_CURRENT_TIME],
        tool_handlers={"get_current_time": handler_instrumentado},
        temperature=0.0,
        custom_api_key=chave,
    )
    print(f"  RESPOSTA: {resposta_b.strip()[:80]}")
    checar(len(INVOCACOES) == antes, "nao chamou ferramenta quando nao precisava")
    checar(bool(resposta_b and resposta_b.strip()), "resposta do caso B nao veio vazia")

    print("  (pausa de 20s: free tier limita a 5 requisicoes/minuto)")
    await asyncio.sleep(20)

    # ---------- CASO C: sem tools, comportamento antigo intacto ----------
    print("\n[CASO C] Sem 'tools' - compatibilidade com o webhook atual")
    resposta_c = await llm_service.generate_response(
        system_prompt="Voce e um assistente objetivo.",
        user_message="Responda apenas com a palavra: ok",
        context_chunks=["Este e um trecho de contexto de teste."],
        temperature=0.0,
        custom_api_key=chave,
    )
    print(f"  RESPOSTA: {resposta_c.strip()[:80]}")
    checar(bool(resposta_c and resposta_c.strip()),
           "chamada sem ferramentas continua funcionando (webhook nao quebra)")

    print("  (pausa de 20s: free tier limita a 5 requisicoes/minuto)")
    await asyncio.sleep(20)

    # ---------- CASO D: handler ausente nao derruba o atendimento ----------
    print("\n[CASO D] Modelo pede funcao sem handler registrado")
    resposta_d = await llm_service.generate_response(
        system_prompt="Voce e um assistente objetivo. Use as ferramentas quando precisar.",
        user_message="Que horas sao agora?",
        tools=[DECLARACAO_GET_CURRENT_TIME],
        tool_handlers={},  # nenhum handler
        temperature=0.0,
        custom_api_key=chave,
    )
    print(f"  RESPOSTA: {resposta_d.strip()[:120]}")
    checar(bool(resposta_d and resposta_d.strip()),
           "falha de ferramenta degrada com resposta, nao com excecao")


if __name__ == "__main__":
    asyncio.run(principal())
    print("\n" + "=" * 60)
    if FALHAS:
        print(f"TESTE 3: FALHOU ({len(FALHAS)} verificacao(oes))")
        for f in FALHAS:
            print(f"  - {f}")
        sys.exit(1)
    print("TESTE 3: PASSOU")
    sys.exit(0)
