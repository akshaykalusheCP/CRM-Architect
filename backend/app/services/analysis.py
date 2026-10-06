"""Orchestrates discovery/refinement runs and persists their results."""

import asyncio
import hashlib
import logging
from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_adapter
from app.core.config import get_settings
from app.db.models import (
    Job,
    ModelVersion,
    ModelVersionSource,
    Project,
    ProjectStatus,
    Question,
    QuestionStatus,
    SourceFile,
    SourceKindDb,
    SourceStatus,
)
from app.domain.business_model import BusinessModel, DiscoveryResult
from app.domain.validation import Issue, normalize_model, validate_model
from app.ingestion.profiler import load_key_values
from app.ingestion.relationships import infer_relationships
from app.llm.factory import get_provider_for_org
from app.pipeline.discovery import AnalysisContext, SourceContext, run_discovery
from app.services.jobs import set_progress
from app.services.knowledge import retrieve_knowledge
from app.services.sources import process_source
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


async def latest_version(session: AsyncSession, project_id: str) -> ModelVersion | None:
    return await session.scalar(
        select(ModelVersion).where(ModelVersion.project_id == project_id).order_by(ModelVersion.version.desc())
    )


async def _next_version(session: AsyncSession, project_id: str) -> int:
    current = await session.scalar(select(func.max(ModelVersion.version)).where(ModelVersion.project_id == project_id))
    return (current or 0) + 1


async def save_version(
    session: AsyncSession,
    project: Project,
    model: BusinessModel,
    *,
    source: ModelVersionSource,
    summary: str,
    changes: list[str],
    issues: list[Issue],
    usage: dict | None,
    user_id: str | None,
) -> ModelVersion:
    version = ModelVersion(
        project_id=project.id,
        version=await _next_version(session, project.id),
        source=source,
        model=model.model_dump(mode="json"),
        summary=summary,
        changes=changes,
        issues=[i.to_dict() for i in issues],
        llm_usage=usage,
        created_by=user_id,
    )
    session.add(version)
    project.current_version = version.version
    return version


def _relationships(sources: list[SourceFile]) -> list[dict]:
    settings = get_settings()
    storage = get_storage()
    key_values: dict[tuple[str, str, str], set[str]] = {}
    for s in sources:
        if s.kind != SourceKindDb.tabular or s.status != SourceStatus.processed:
            continue
        for (sheet, col), values in load_key_values(
            s.filename, storage.read(s.storage_key), settings.max_profile_rows
        ).items():
            key_values[(s.filename, sheet, col)] = values
    return infer_relationships(key_values)


async def build_context(session: AsyncSession, project: Project) -> AnalysisContext:
    settings = get_settings()
    sources = list(
        (
            await session.scalars(
                select(SourceFile).where(SourceFile.project_id == project.id).order_by(SourceFile.created_at)
            )
        ).all()
    )
    for s in sources:
        if s.status in (SourceStatus.uploaded, SourceStatus.processing):
            await process_source(session, s)

    source_ctx = []
    for s in sources:
        if s.status != SourceStatus.processed:
            continue
        text = s.extracted_text
        truncated = bool(text and len(text) > settings.max_document_chars)
        if truncated and text:
            text = text[: settings.max_document_chars]
        source_ctx.append(SourceContext(s.filename, s.kind.value, s.profile, text, truncated))

    questions = (await session.scalars(select(Question).where(Question.project_id == project.id))).all()
    previous = await latest_version(session, project.id)
    last_inputs = await _last_ai_inputs(session, project.id)
    knowledge = await retrieve_knowledge(
        session,
        project,
        " ".join(
            c.get("name", "")
            for source in source_ctx if source.profile
            for sheet in source.profile.get("sheets", [])
            for c in sheet.get("columns", [])
        ),
    )
    return AnalysisContext(
        project={
            "name": project.name,
            "client_name": project.client_name,
            "industry": project.industry,
            "target_platform": project.target_platform,
            "description": project.description,
        },
        knowledge=knowledge,
        sources=source_ctx,
        relationships=await asyncio.to_thread(_relationships, sources),
        answered=[
            {"key": q.key, "question": q.text, "answer": q.answer}
            for q in questions
            if q.status == QuestionStatus.answered
        ],
        dismissed=[{"key": q.key, "question": q.text} for q in questions if q.status == QuestionStatus.dismissed],
        open_question_keys=[q.key for q in questions if q.status == QuestionStatus.open],
        previous_model=previous.model if previous else None,
        previous_summary=previous.summary if previous else None,
        new_answer_keys=[
            q.key for q in questions if q.status == QuestionStatus.answered and q.applied_in_version is None
        ],
        inputs_changed=last_inputs != inputs_fingerprint(project, sources, knowledge),
    )


def inputs_fingerprint(project: Project, sources: list[SourceFile], knowledge: list[dict] | None = None) -> str:
    """Identifies the business input (narrative + processed files) a design was generated from."""
    parts = [project.description.strip(), project.industry or "", project.target_platform]
    parts += sorted(f"{s.id}:{s.size_bytes}" for s in sources if s.status == SourceStatus.processed)
    parts += sorted(f"{item['id']}:{item['content']}" for item in knowledge or [])
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


async def _last_ai_inputs(session: AsyncSession, project_id: str) -> str | None:
    rows = await session.scalars(
        select(ModelVersion).where(ModelVersion.project_id == project_id).order_by(ModelVersion.version.desc())
    )
    for v in rows:
        if v.llm_usage and v.llm_usage.get("inputs"):
            return v.llm_usage["inputs"]
    return None


async def _sync_questions(
    session: AsyncSession,
    project: Project,
    result: DiscoveryResult,
    version: int,
    consumed_answer_keys: set[str],
    keep_open: bool = False,
) -> int:
    existing = {
        q.key: q for q in (await session.scalars(select(Question).where(Question.project_id == project.id))).all()
    }
    new_keys = {q.key for q in result.questions}
    # Open questions the model no longer needs are resolved; answered ones stay as history.
    for q in existing.values():
        if q.status == QuestionStatus.open and q.key not in new_keys and not keep_open:
            q.status = QuestionStatus.resolved
        if q.status == QuestionStatus.answered and q.key in consumed_answer_keys:
            q.applied_in_version = version
    created = 0
    for q in result.questions:
        row = existing.get(q.key)
        if row and row.status in (QuestionStatus.answered, QuestionStatus.dismissed):
            continue
        if row is None:
            row = Question(project_id=project.id, key=q.key)
            session.add(row)
            created += 1
        row.text, row.why = q.question, q.why
        row.category, row.priority = str(q.category), q.priority
        row.suggested_answers, row.related = q.suggested_answers, q.related
        row.status, row.asked_in_version = QuestionStatus.open, version
    return created


async def run_analysis_job(session: AsyncSession, job: Job) -> dict:
    project = await session.get(Project, job.project_id)
    assert project is not None
    project.status = ProjectStatus.analyzing
    await set_progress(session, job, "Reading sources")
    ctx = await build_context(session, project)
    if not ctx.sources and not project.description.strip() and not ctx.answered:
        raise ValueError("Add a business description or upload at least one file before analysing")

    await set_progress(session, job, "Refining the design" if ctx.is_refine else "Designing the CRM")
    settings = get_settings()
    provider = await get_provider_for_org(session, job.org_id)

    async def progress(message: str) -> None:
        await set_progress(session, job, message)

    adapter = get_adapter(project.target_platform)
    outcome = await run_discovery(
        provider,
        ctx,
        repair_attempts=settings.llm_repair_attempts,
        progress=progress,
        single_call_budget=settings.llm_max_output_tokens + 25_000,
        automation_guidance=adapter.automation_guidance if adapter else None,
    )

    version = await save_version(
        session,
        project,
        outcome.result.model,
        source=ModelVersionSource.ai_refine if ctx.is_refine else ModelVersionSource.ai_discovery,
        summary=outcome.result.summary,
        changes=outcome.result.changes,
        issues=outcome.issues,
        usage={
            **asdict(outcome.usage),
            "provider": provider.name,
            "model": outcome.model_name,
            "attempts": outcome.attempts,
            "inputs": inputs_fingerprint(project, await _processed_sources(session, project.id), ctx.knowledge),
            "knowledge_ids": [item["id"] for item in ctx.knowledge],
            "targeted": outcome.keep_open_questions,
        },
        user_id=job.created_by,
    )
    new_questions = await _sync_questions(
        session, project, outcome.result, version.version, {a["key"] for a in ctx.answered}, outcome.keep_open_questions
    )
    open_count = await session.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.project_id == project.id, Question.status == QuestionStatus.open)
    )
    project.status = ProjectStatus.needs_input if open_count else ProjectStatus.ready
    await session.commit()
    return {
        "version": version.version,
        "new_questions": new_questions,
        "open_questions": open_count,
        "errors": sum(1 for i in outcome.issues if i.severity == "error"),
        "warnings": sum(1 for i in outcome.issues if i.severity == "warning"),
    }


async def _processed_sources(session: AsyncSession, project_id: str) -> list[SourceFile]:
    return list((await session.scalars(select(SourceFile).where(SourceFile.project_id == project_id))).all())


async def on_job_failed(session: AsyncSession, job: Job) -> None:
    if job.type.value != "analyze":
        return
    project = await session.get(Project, job.project_id)
    if project is not None:
        project.status = ProjectStatus.ready if project.current_version else ProjectStatus.error


def revalidate(model: BusinessModel) -> tuple[BusinessModel, list[Issue]]:
    model = normalize_model(model)
    return model, validate_model(model)
