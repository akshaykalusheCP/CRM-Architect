"""Background job dispatch: arq/Redis in production, in-process tasks for dev and tests."""

import asyncio
import logging
import weakref
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.db.models import Job, JobStatus, JobType

logger = logging.getLogger(__name__)
_inline_tasks: set[asyncio.Task] = set()
_arq_pool = None


async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _arq_pool


async def close_arq_pool() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None


async def create_job(
    session: AsyncSession, *, org_id: str, project_id: str, type: JobType, payload: dict, user_id: str | None
) -> Job:
    job = Job(org_id=org_id, project_id=project_id, type=type, payload=payload, created_by=user_id)
    session.add(job)
    await session.flush()
    return job


async def dispatch(job: Job) -> None:
    """Call after the transaction that created the job has committed."""
    if get_settings().job_backend == "inline":
        task = asyncio.create_task(execute_job(job.id))
        _inline_tasks.add(task)
        task.add_done_callback(_inline_tasks.discard)
        return
    pool = await _get_arq_pool()
    await pool.enqueue_job("run_job", job.id, _job_id=job.id)


async def wait_for_inline_jobs() -> None:
    while _inline_tasks:
        await asyncio.gather(*list(_inline_tasks), return_exceptions=True)


async def execute_job(job_id: str) -> None:
    from app.services.analysis import on_job_failed, run_analysis_job
    from app.services.sources import run_process_source_job

    handlers = {JobType.analyze: run_analysis_job, JobType.process_source: run_process_source_job}
    sm = get_sessionmaker()
    async with sm() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status not in (JobStatus.queued, JobStatus.running):
            return
        job.status = JobStatus.running
        job.started_at = datetime.now(UTC)
        await session.commit()
        try:
            result = await handlers[job.type](session, job)
            job.status = JobStatus.succeeded
            job.result = result
            job.progress = "Done"
            job.finished_at = datetime.now(UTC)
            await session.commit()
            return
        except Exception as exc:  # noqa: BLE001 - every failure must be recorded on the job
            logger.exception("job failed", extra={"job_id": job_id})
            error = str(exc)[:2000] or exc.__class__.__name__
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 - the session may itself be what broke
                logger.warning("could not roll back the job session", extra={"job_id": job_id})

    # Record the failure through a fresh session so a broken one cannot leave the job "running" forever.
    async with sm() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.failed
        job.error = error
        job.finished_at = datetime.now(UTC)
        await on_job_failed(session, job)
        await session.commit()


_progress_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _progress_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _progress_locks.get(loop)
    if lock is None:
        lock = _progress_locks[loop] = asyncio.Lock()
    return lock


async def set_progress(session: AsyncSession, job: Job, message: str) -> None:
    """Publish a progress message for the UI.

    Analysis steps run in parallel and report concurrently, so this writes through its own short-lived
    session (serialised by a lock) instead of the job's session, which must never be used concurrently.
    """
    async with _progress_lock(), get_sessionmaker()() as progress_session:
        await progress_session.execute(update(Job).where(Job.id == job.id).values(progress=message[:200]))
        await progress_session.commit()
