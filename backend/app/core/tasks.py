import asyncio
from datetime import datetime, timezone

from celery.utils.log import get_task_logger
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.config import settings

logger = get_task_logger("atendit.tasks")


def _rodar_isolado(coro):
    """
    Executa uma corrotina numa task Celery e DESCARTA o pool de conexoes no fim.

    asyncio.run() cria um event loop NOVO a cada chamada, mas o engine do
    SQLAlchemy e global e seu pool asyncpg fica preso ao loop que o criou.
    Da segunda execucao em diante, reaproveitar aquelas conexoes levanta
    "got Future attached to a different loop" -- falha intermitente que so
    aparece na SEGUNDA vez, e por isso passa nos primeiros testes.

    engine.dispose() fecha tudo, e a proxima task abre conexoes no seu
    proprio loop.
    """
    async def _envelope():
        try:
            return await coro
        finally:
            from app.core.database import engine
            await engine.dispose()

    return asyncio.run(_envelope())


@celery_app.task(name="app.core.tasks.heartbeat")
def heartbeat() -> dict:
    """
    Task de sinal de vida. Disparada pelo Celery Beat a cada 60 segundos.

    Existe para provar que a esteira Beat -> Redis -> Worker esta completa:
    se esta linha aparece no log do worker, o agendador publicou na fila e o
    worker consumiu. Nao toca em dado de producao.
    """
    agora = datetime.now(timezone.utc)
    logger.info(f"[CELERY HEARTBEAT] batimento registrado em {agora.isoformat()}")
    return {"status": "ok", "timestamp": agora.isoformat()}


@celery_app.task(name="app.core.tasks.checar_conexoes")
def checar_conexoes() -> dict:
    """
    Prova de conectividade do worker com Postgres e Redis.

    Somente leitura: consulta a versao do servidor e conta linhas de tenants,
    e faz PING no Redis. Nao escreve nada.
    """
    resultado = {"postgres": "nao verificado", "redis": "nao verificado"}

    async def _checar_postgres() -> str:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                versao = (await conn.execute(text("SELECT version()"))).scalar_one()
                total = (await conn.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
            return f"OK | {versao.split(',')[0]} | tenants={total}"
        finally:
            await engine.dispose()

    try:
        resultado["postgres"] = asyncio.run(_checar_postgres())
    except Exception as exc:
        resultado["postgres"] = f"FALHOU: {exc}"

    try:
        import redis as redis_lib

        cliente = redis_lib.Redis.from_url(settings.REDIS_URL)
        cliente.ping()
        resultado["redis"] = f"OK | PING respondido | dbsize={cliente.dbsize()}"
        cliente.close()
    except Exception as exc:
        resultado["redis"] = f"FALHOU: {exc}"

    logger.info(f"[CELERY CONEXOES] postgres={resultado['postgres']} | redis={resultado['redis']}")
    return resultado


# --------------------------------------------------------------------------
# Lista de espera
# --------------------------------------------------------------------------
@celery_app.task(name="app.core.tasks.ofertar_vaga")
def ofertar_vaga(tenant_id: str, service_type_id: str, inicio_iso: str, fim_iso: str) -> dict:
    """
    Oferta o horario vago ao melhor candidato e AGENDA o timeout.

    O timeout e agendado aqui, e nao pelo chamador, porque so aqui se sabe
    QUEM foi notificado e qual o prazo do servico dele.
    """
    import uuid as _uuid
    from datetime import datetime as _dt
    from app.services import waitlist_service as w

    r = _rodar_isolado(w.ofertar_proximo(
        tenant_id=_uuid.UUID(tenant_id),
        service_type_id=_uuid.UUID(service_type_id),
        inicio=_dt.fromisoformat(inicio_iso),
        fim=_dt.fromisoformat(fim_iso),
    ))
    if r.get("ofertado"):
        celery_app.send_task(
            "app.core.tasks.expirar_oferta",
            args=[r["waitlist_id"]],
            countdown=int(r["timeout_minutos"]) * 60,
        )
        logger.info(f"[WAITLIST] timeout agendado para daqui a {r['timeout_minutos']}min.")
    return r


@celery_app.task(name="app.core.tasks.expirar_oferta")
def expirar_oferta(waitlist_id: str) -> dict:
    """Expira a oferta nao confirmada e repassa ao proximo da fila."""
    import uuid as _uuid
    from app.services import waitlist_service as w

    r = _rodar_isolado(w.expirar_e_repassar(_uuid.UUID(waitlist_id)))
    proximo = r.get("proximo") or {}
    if proximo.get("ofertado"):
        celery_app.send_task(
            "app.core.tasks.expirar_oferta",
            args=[proximo["waitlist_id"]],
            countdown=int(proximo["timeout_minutos"]) * 60,
        )
    return r


@celery_app.task(name="app.core.tasks.enviar_lembretes")
def enviar_lembretes() -> dict:
    """Varre os compromissos na janela de lembrete e envia o que falta."""
    from app.services import reminder_service

    r = _rodar_isolado(reminder_service.enviar_lembretes_pendentes())
    if r.get("verificados"):
        logger.info(f"[LEMBRETE] {r}")
    return r
