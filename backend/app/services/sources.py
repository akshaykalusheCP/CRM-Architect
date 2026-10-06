import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Job, SourceFile, SourceKindDb, SourceStatus
from app.ingestion.documents import DOCUMENT_EXTENSIONS, extract_text
from app.ingestion.profiler import TABULAR_EXTENSIONS, profile_tabular
from app.services.storage import get_storage

ALLOWED_EXTENSIONS = TABULAR_EXTENSIONS | DOCUMENT_EXTENSIONS


def classify(filename: str) -> SourceKindDb | None:
    ext = Path(filename).suffix.lower()
    if ext in TABULAR_EXTENSIONS:
        return SourceKindDb.tabular
    if ext in DOCUMENT_EXTENSIONS:
        return SourceKindDb.document
    return None


def _process(source_kind: SourceKindDb, filename: str, data: bytes) -> tuple[dict | None, str | None]:
    settings = get_settings()
    if source_kind == SourceKindDb.tabular:
        return profile_tabular(filename, data, settings.max_profile_rows, settings.llm_mask_pii_samples), None
    text = extract_text(filename, data)
    return {"type": "document", "chars": len(text), "truncated": len(text) > settings.max_document_chars}, text


async def process_source(session: AsyncSession, source: SourceFile) -> None:
    source.status = SourceStatus.processing
    await session.commit()
    try:
        data = get_storage().read(source.storage_key)
        # pandas / pdf parsing is CPU-bound; keep the event loop responsive.
        profile, text = await asyncio.to_thread(_process, source.kind, source.filename, data)
        source.profile = profile
        source.extracted_text = text
        source.status = SourceStatus.processed
        source.error = None
    except Exception as exc:  # noqa: BLE001 - surface parse errors on the file record
        source.status = SourceStatus.failed
        source.error = str(exc)[:1000]
    await session.commit()


async def run_process_source_job(session: AsyncSession, job: Job) -> dict:
    source = await session.get(SourceFile, job.payload["source_id"])
    if source is None:
        return {"skipped": True}
    await process_source(session, source)
    return {"source_id": source.id, "status": source.status.value, "error": source.error}
