"""Browse and curate organization knowledge, including Salesforce metadata imports."""

import io
import zipfile
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.deps import SessionDep, UserDep
from app.db.models import KnowledgeEntry, Project, User, UserRole
from app.ingestion.salesforce_metadata import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    is_supported_path,
    parse_salesforce_bundle,
    summarize_category,
    summarize_object,
)
from app.services.knowledge import BUILTIN

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
MAX_UPLOAD_BYTES = 50_000_000


def _admin(user: User) -> None:
    if user.role not in (UserRole.owner, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and admins can manage organization knowledge")


def _out(row: KnowledgeEntry) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "industry": row.industry,
        "kind": row.kind,
        "source_name": row.source_name,
        "structured": row.structured,
        "status": row.status,
        "scope": "organization" if row.project_id is None else "project",
        "origin_project_id": row.origin_project_id,
        "created_at": row.created_at,
    }


@router.get("")
async def list_knowledge(user: UserDep, session: SessionDep):
    rows = (
        await session.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.org_id == user.org_id)
            .order_by(KnowledgeEntry.created_at.desc())
        )
    ).all()
    project_industries = (
        await session.scalars(
            select(Project.industry).where(Project.org_id == user.org_id, Project.industry.is_not(None))
        )
    ).all()
    return {
        "can_manage": user.role in (UserRole.owner, UserRole.admin),
        "entries": [_out(row) for row in rows],
        "builtin": [{"id": f"builtin:{i}", "title": title, "content": content}
                    for i, (title, content) in enumerate(BUILTIN)],
        "industries": sorted(set(project_industries) | {row.industry for row in rows if row.industry}),
    }


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_metadata(
    user: UserDep,
    session: SessionDep,
    industry: Annotated[str, Form(min_length=2, max_length=120)],
    file: Annotated[UploadFile, File()],
):
    _admin(user)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload a Salesforce metadata ZIP")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Metadata ZIP must be under 50 MB")
    try:
        objects, categories = parse_salesforce_bundle(data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await _save_import(objects, categories, industry, file.filename, user, session)


async def _save_import(objects: list[dict], categories: list[dict], industry: str, source_name: str, user: User, session: SessionDep):
    source_name = source_name[:255]
    industry = industry.strip()
    existing = (
        await session.scalars(
            select(KnowledgeEntry).where(
                KnowledgeEntry.org_id == user.org_id,
                KnowledgeEntry.source_name == source_name,
                KnowledgeEntry.industry == industry,
                KnowledgeEntry.kind.in_(("salesforce_metadata", "salesforce_category")),
            )
        )
    ).all()
    known = {(row.kind, row.structured.get("object_api_name") or row.structured.get("metadata_type")):
             row.structured for row in existing if row.structured}
    rows = [
        KnowledgeEntry(
            org_id=user.org_id,
            title=f"{obj['label']} ({obj['object_api_name']})",
            content=summarize_object(obj),
            industry=industry,
            kind="salesforce_metadata",
            source_name=source_name,
            structured=obj,
            status="pending",
            created_by=user.id,
        )
        for obj in objects
        if known.get(("salesforce_metadata", obj["object_api_name"])) != obj
    ]
    rows.extend(
        KnowledgeEntry(
            org_id=user.org_id,
            title=f"Salesforce {category['title']}",
            content=summarize_category(category),
            industry=industry,
            kind="salesforce_category",
            source_name=source_name,
            structured=category,
            status="pending",
            created_by=user.id,
        )
        for category in categories
        if known.get(("salesforce_category", category["metadata_type"])) != category
    )
    session.add_all(rows)
    await session.commit()
    return {"imported": len(rows), "entries": [_out(row) for row in rows]}


@router.post("/import-folder", status_code=status.HTTP_201_CREATED)
async def import_folder(
    request: Request,
    user: UserDep,
    session: SessionDep,
):
    _admin(user)
    async with request.form(max_files=MAX_FILES, max_fields=MAX_FILES + 5) as form:
        industry = form.get("industry")
        paths = form.getlist("paths")
        files = form.getlist("files")
        if not isinstance(industry, str) or not 2 <= len(industry.strip()) <= 120:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose an industry")
        if len(files) != len(paths) or not files or not all(
            isinstance(path, str) and isinstance(file, StarletteUploadFile)
            for path, file in zip(paths, files, strict=True)
        ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Folder files and paths do not match")
        total = 0
        names: set[str] = set()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for path, file in zip(paths, files, strict=True):
                normalized = path.replace("\\", "/").lstrip("/")
                if not is_supported_path(normalized) or ".." in normalized.split("/") or normalized in names:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid metadata path: {path[:120]}")
                names.add(normalized)
                data = await file.read(1_000_001)
                if len(data) > 1_000_000:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Metadata XML exceeds 1 MB: {path[:120]}")
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Folder metadata exceeds 100 MB")
                archive.writestr(normalized, data)
    try:
        objects, categories = parse_salesforce_bundle(buffer.getvalue())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    root = paths[0].replace("\\", "/").split("/", 1)[0]
    return await _save_import(objects, categories, industry, f"Folder: {root}", user, session)


class KnowledgePatch(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=10, max_length=4000)
    industry: str | None = Field(default=None, max_length=120)


@router.patch("/{entry_id}")
async def edit_knowledge(entry_id: str, body: KnowledgePatch, user: UserDep, session: SessionDep):
    _admin(user)
    row = await session.scalar(
        select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id, KnowledgeEntry.org_id == user.org_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge entry not found")
    row.title, row.content, row.industry = (
        body.title.strip(), body.content.strip(), body.industry.strip() if body.industry else None
    )
    # Editing approved knowledge requires another review before it can guide analysis.
    row.status = "pending"
    row.reviewed_by = None
    row.reviewed_at = None
    await session.commit()
    return _out(row)


class KnowledgeReview(BaseModel):
    decision: Literal["approve", "reject"]
    scope: Literal["project", "organization"] = "project"


class BulkKnowledgeReview(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    decision: Literal["approve", "reject"]


@router.post("/review-bulk")
async def review_bulk(body: BulkKnowledgeReview, user: UserDep, session: SessionDep):
    _admin(user)
    if len(set(body.ids)) != len(body.ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Duplicate knowledge IDs")
    rows = (
        await session.scalars(
            select(KnowledgeEntry).where(KnowledgeEntry.id.in_(body.ids), KnowledgeEntry.org_id == user.org_id)
        )
    ).all()
    if len(rows) != len(body.ids) or any(row.status != "pending" or row.kind not in ("salesforce_metadata", "salesforce_category") for row in rows):
        raise HTTPException(status.HTTP_409_CONFLICT, "Some imported entries are missing or already reviewed")
    if len({(row.source_name, row.industry) for row in rows}) != 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Review one source and industry at a time")
    now = datetime.now(UTC)
    for row in rows:
        row.status = "approved" if body.decision == "approve" else "rejected"
        row.reviewed_by = user.id
        row.reviewed_at = now
    await session.commit()
    return {"reviewed": len(rows), "status": rows[0].status}


@router.post("/{entry_id}/review")
async def review_knowledge(entry_id: str, body: KnowledgeReview, user: UserDep, session: SessionDep):
    _admin(user)
    row = await session.scalar(
        select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id, KnowledgeEntry.org_id == user.org_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Knowledge entry not found")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Entry has already been reviewed")
    row.status = "approved" if body.decision == "approve" else "rejected"
    if body.decision == "approve" and body.scope == "organization":
        row.project_id = None
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    await session.commit()
    return _out(row)
