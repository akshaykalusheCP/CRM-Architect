"""Project feedback with explicit review before it can guide future analyses."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ProjectDep, SessionDep, UserDep
from app.db.models import KnowledgeEntry, ModelVersion, UserRole

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["knowledge"])


class FeedbackIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=10, max_length=2000)
    source_version: int | None = None


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    scope: Literal["project", "organization"] = "project"


def _out(entry: KnowledgeEntry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "status": entry.status,
        "scope": "organization" if entry.project_id is None else "project",
        "source_version": entry.source_version,
        "industry": entry.industry,
        "kind": entry.kind,
        "source_name": entry.source_name,
        "structured": entry.structured,
        "created_at": entry.created_at,
    }


@router.get("")
async def list_feedback(project: ProjectDep, session: SessionDep):
    rows = (
        await session.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.org_id == project.org_id, KnowledgeEntry.origin_project_id == project.id)
            .order_by(KnowledgeEntry.created_at.desc())
        )
    ).all()
    return [_out(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_feedback(body: FeedbackIn, project: ProjectDep, user: UserDep, session: SessionDep):
    if body.source_version is not None:
        exists = await session.scalar(
            select(ModelVersion.id).where(
                ModelVersion.project_id == project.id, ModelVersion.version == body.source_version
            )
        )
        if not exists:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Design version not found")
    entry = KnowledgeEntry(
        org_id=project.org_id,
        project_id=project.id,
        origin_project_id=project.id,
        source_version=body.source_version,
        title=body.title.strip(),
        content=body.content.strip(),
        industry=project.industry,
        kind="feedback",
        status="pending",
        created_by=user.id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return _out(entry)


@router.post("/{entry_id}/review")
async def review_feedback(
    entry_id: str, body: ReviewIn, project: ProjectDep, user: UserDep, session: SessionDep
):
    if user.role not in (UserRole.owner, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and admins can review feedback")
    entry = await session.scalar(
        select(KnowledgeEntry).where(
            KnowledgeEntry.id == entry_id,
            KnowledgeEntry.org_id == project.org_id,
            KnowledgeEntry.project_id == project.id,
        )
    )
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feedback not found")
    if entry.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Feedback has already been reviewed")
    entry.status = "approved" if body.decision == "approve" else "rejected"
    if body.decision == "approve" and body.scope == "organization":
        entry.project_id = None
    entry.reviewed_by = user.id
    entry.reviewed_at = datetime.now(UTC)
    await session.commit()
    return _out(entry)
