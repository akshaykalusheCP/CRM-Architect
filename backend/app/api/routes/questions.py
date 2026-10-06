from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, select

from app.api.deps import ProjectDep, SessionDep, UserDep
from app.api.schemas import QuestionOut, QuestionPatch
from app.db.models import Question, QuestionStatus

router = APIRouter(prefix="/projects/{project_id}/questions", tags=["questions"])

_PRIORITY = case({"high": 0, "medium": 1, "low": 2}, value=Question.priority, else_=3)
_STATUS = case({"open": 0, "answered": 1, "dismissed": 2, "resolved": 3}, value=Question.status, else_=4)


@router.get("", response_model=list[QuestionOut])
async def list_questions(project: ProjectDep, session: SessionDep):
    rows = await session.scalars(
        select(Question).where(Question.project_id == project.id).order_by(_STATUS, _PRIORITY, Question.created_at)
    )
    return rows.all()


@router.patch("/{question_id}", response_model=QuestionOut)
async def update_question(
    question_id: str, body: QuestionPatch, project: ProjectDep, user: UserDep, session: SessionDep
):
    q = await session.scalar(select(Question).where(Question.id == question_id, Question.project_id == project.id))
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    new_status = body.status or ("answered" if body.answer else None)
    if new_status == "answered":
        if not (body.answer or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An answer is required")
        q.answer = body.answer.strip()
        q.status = QuestionStatus.answered
        q.applied_in_version = None
        q.answered_by, q.answered_at = user.id, datetime.now(UTC)
    elif new_status == "dismissed":
        q.status = QuestionStatus.dismissed
    elif new_status == "open":
        q.status, q.answer, q.answered_by, q.answered_at = QuestionStatus.open, None, None, None
        q.applied_in_version = None
    await session.commit()
    return q
