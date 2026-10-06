import asyncio

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select

from app.adapters.registry import get_adapter
from app.api.deps import ProjectDep, SessionDep, UserDep
from app.api.schemas import ExportIn, ExportOut
from app.db.models import Export, ModelVersion, Question, QuestionStatus
from app.domain.business_model import BusinessModel
from app.domain.validation import Issue
from app.exports.design_doc import render_design_doc
from app.services.storage import get_storage, safe_filename

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["exports"])


@router.get("", response_model=list[ExportOut])
async def list_exports(project: ProjectDep, session: SessionDep):
    rows = await session.scalars(
        select(Export).where(Export.project_id == project.id).order_by(Export.created_at.desc())
    )
    return rows.all()


@router.post("", response_model=ExportOut, status_code=status.HTTP_201_CREATED)
async def create_export(body: ExportIn, project: ProjectDep, user: UserDep, session: SessionDep):
    number = body.version or project.current_version
    version = await session.scalar(
        select(ModelVersion).where(ModelVersion.project_id == project.id, ModelVersion.version == number)
    )
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No design version to export")
    issues = [Issue(**i) for i in version.issues]
    model = BusinessModel.model_validate(version.model)
    slug = safe_filename(project.name.replace(" ", "_"))

    if body.kind == "design_doc":
        questions = (
            await session.scalars(
                select(Question).where(Question.project_id == project.id, Question.status == QuestionStatus.open)
            )
        ).all()
        content = render_design_doc(
            model,
            project.name,
            version.version,
            version.summary,
            issues,
            [{"text": q.text, "priority": q.priority} for q in questions],
            version.llm_usage,
        ).encode()
        filename, report = f"{slug}_design_v{version.version}.md", {"format": "markdown"}
    else:
        adapter = get_adapter(body.kind)
        if adapter is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Export for '{body.kind}' is not available")
        if any(i.severity == "error" for i in issues):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Fix the design's validation errors before generating platform metadata",
            )
        artifact = await asyncio.to_thread(adapter.generate, model, project.name)
        root = f"{slug}_{adapter.key}_v{version.version}"
        content = await asyncio.to_thread(artifact.to_zip, root)
        filename, report = f"{root}.zip", artifact.report()

    storage = get_storage()
    key = storage.new_key(f"{user.org_id}/{project.id}/exports", filename)
    storage.save(key, content)
    export = Export(
        project_id=project.id,
        version=version.version,
        kind=body.kind,
        filename=filename,
        storage_key=key,
        report=report,
        created_by=user.id,
    )
    session.add(export)
    await session.commit()
    return export


@router.get("/{export_id}/download")
async def download_export(export_id: str, project: ProjectDep, session: SessionDep):
    export = await session.scalar(select(Export).where(Export.id == export_id, Export.project_id == project.id))
    if export is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    media = "application/zip" if export.filename.endswith(".zip") else "text/markdown; charset=utf-8"
    return Response(
        get_storage().read(export.storage_key),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )
