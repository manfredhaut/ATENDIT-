from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "atendit_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    # Sem 'include' o worker sobe com ZERO tasks registradas e o Beat publica
    # numa fila que ninguem consome, sem emitir erro.
    include=["app.core.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    # Celery 6 deixa de reconectar ao broker no startup por padrao; sem isto o
    # worker morre se subir antes do Redis estar pronto.
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "heartbeat-a-cada-minuto": {
            "task": "app.core.tasks.heartbeat",
            "schedule": 60.0,
        },
                "lembretes-logisticos-a-cada-5-minutos": {
            "task": "app.core.tasks.verificar_lembretes_logisticos",
            "schedule": 300.0,
        },
        "lembretes-a-cada-5-minutos": {
            "task": "app.core.tasks.enviar_lembretes",
            "schedule": 300.0,
        },
    },
)
