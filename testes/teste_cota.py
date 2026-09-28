"""
Prova empirica de que a cota da chave nova e separada.

25 chamadas sequenciais espacadas ~13s (=~4,6/min, abaixo do teto de 5/min do
free tier). Se passar de 20 sem 429 de cota DIARIA, o projeto e outro.

Le a chave do banco, nao de argumento: chave em argv aparece em `ps`.
"""

import asyncio
import re
import sys
import time
import uuid

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.tenant import AIConfig

from app.services.llm_service import llm_service

TENANT = uuid.UUID("21e7ca95-2a64-48e9-9882-ddfb3cc62cba")
TOTAL = 25
INTERVALO = 13.0


async def principal() -> None:
    async with AsyncSessionLocal() as sessao:
        cfg = (
            await sessao.execute(select(AIConfig).where(AIConfig.tenant_id == TENANT))
        ).scalar_one()

    import hashlib

    print(f"chave usada: sha256[0:16]={hashlib.sha256(cfg.api_key.encode()).hexdigest()[:16]}")
    print(f"modelo: {cfg.model} | {TOTAL} chamadas, uma a cada {INTERVALO:.0f}s\n", flush=True)

    sucesso = 0
    quota_minuto = 0
    quota_dia = 0
    outros = 0

    for i in range(1, TOTAL + 1):
        t0 = time.time()
        try:
            r = await llm_service.generate_response(
                system_prompt="Assistente objetivo.",
                user_message="responda apenas OK",
                custom_api_key=cfg.api_key,
                model=cfg.model,
                temperature=0.0,
            )
            sucesso += 1
            print(
                f"  {i:2d}/{TOTAL}  OK    resp={r.strip()[:12]!r:16} "
                f"| sucesso={sucesso} 429min={quota_minuto} 429dia={quota_dia}",
                flush=True,
            )
        except Exception as exc:
            msg = str(exc)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                qid = re.search(r"'quotaId': '([^']+)'", msg)
                nome = qid.group(1) if qid else "?"
                if "PerDay" in nome:
                    quota_dia += 1
                else:
                    quota_minuto += 1
                print(
                    f"  {i:2d}/{TOTAL}  429   {nome:52} "
                    f"| sucesso={sucesso} 429min={quota_minuto} 429dia={quota_dia}",
                    flush=True,
                )
            else:
                outros += 1
                print(f"  {i:2d}/{TOTAL}  ERRO  {msg[:70]}", flush=True)

        if i < TOTAL:
            await asyncio.sleep(max(0.0, INTERVALO - (time.time() - t0)))

    print()
    print("=" * 64)
    print(f"RESUMO: sucesso={sucesso}  429_por_minuto={quota_minuto}  "
          f"429_por_DIA={quota_dia}  outros={outros}  (de {TOTAL})")
    if sucesso > 20 and quota_dia == 0:
        print("VEREDITO: passou de 20 chamadas SEM 429 diario -> COTA SEPARADA (projeto novo).")
    elif quota_dia > 0:
        print("VEREDITO: bateu 429 de cota DIARIA -> mesmo projeto, ou projeto com cota ja gasta.")
    else:
        print("VEREDITO: inconclusivo - ver contagens acima.")


if __name__ == "__main__":
    asyncio.run(principal())
    sys.exit(0)
