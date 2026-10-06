"""arq worker: `arq app.worker.WorkerSettings`."""

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import configure_logging
from app.services.jobs import execute_job


async def run_job(ctx: dict, job_id: str) -> None:
    await execute_job(job_id)


async def startup(ctx: dict) -> None:
    configure_logging()
    get_engine()


async def shutdown(ctx: dict) -> None:
    await dispose_engine()


class WorkerSettings:
    functions = [run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 30 * 60  # large discovery runs with high effort can take many minutes
    max_tries = 1  # LLM calls are expensive; failures are recorded and retried by the user
