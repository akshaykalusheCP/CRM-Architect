from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select

from app.adapters.registry import get_adapter
from app.api.deps import ProjectDep, SessionDep, UserDep
from app.api.schemas import ModelUpdateIn, VersionOut, VersionSummary
from app.db.models import ModelVersion, ModelVersionSource
from app.domain.business_model import BusinessModel
from app.services.analysis import latest_version, revalidate, save_version

router = APIRouter(prefix="/projects/{project_id}/model", tags=["model"])


def _parse(model: dict) -> BusinessModel:
    try:
        return BusinessModel.model_validate(model)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()[:50]]
        ) from exc


@router.get("", response_model=VersionOut)
async def current_model(project: ProjectDep, session: SessionDep):
    version = await latest_version(session, project.id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No design yet; run an analysis first")
    return version


@router.get("/versions", response_model=list[VersionSummary])
async def list_versions(project: ProjectDep, session: SessionDep):
    rows = (
        await session.scalars(
            select(ModelVersion).where(ModelVersion.project_id == project.id).order_by(ModelVersion.version.desc())
        )
    ).all()
    return [
        VersionSummary(
            version=v.version,
            source=v.source.value,
            summary=v.summary,
            created_at=v.created_at,
            error_count=sum(1 for i in v.issues if i["severity"] == "error"),
            warning_count=sum(1 for i in v.issues if i["severity"] == "warning"),
        )
        for v in rows
    ]


@router.get("/versions/{version}", response_model=VersionOut)
async def get_version(version: int, project: ProjectDep, session: SessionDep):
    row = await session.scalar(
        select(ModelVersion).where(ModelVersion.project_id == project.id, ModelVersion.version == version)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    return row


@router.get("/versions/{version}/build-preview")
async def build_preview(version: int, project: ProjectDep, session: SessionDep):
    """How the target platform's build would realise this version (e.g. which automations become Flows)."""
    row = await session.scalar(
        select(ModelVersion).where(ModelVersion.project_id == project.id, ModelVersion.version == version)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    adapter = get_adapter(project.target_platform)
    if adapter is None:
        return {"platform": project.target_platform, "available": False, "automations": {}}
    model = BusinessModel.model_validate(row.model)
    return {"platform": adapter.key, "available": True, "automations": adapter.automation_status(model)}


@router.post("/validate")
async def validate(body: dict, project: ProjectDep):
    model, issues = revalidate(_parse(body))
    return {"issues": [i.to_dict() for i in issues], "model": model.model_dump(mode="json")}


@router.put("", response_model=VersionOut)
async def save_manual_edit(body: ModelUpdateIn, project: ProjectDep, user: UserDep, session: SessionDep):
    if project.current_version != body.base_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The design changed (now v{project.current_version}); reload and re-apply your edit",
        )
    model, issues = revalidate(_parse(body.model))
    version = await save_version(
        session,
        project,
        model,
        source=ModelVersionSource.manual_edit,
        summary=body.note,
        changes=[body.note],
        issues=issues,
        usage=None,
        user_id=user.id,
    )
    await session.commit()
    return version
