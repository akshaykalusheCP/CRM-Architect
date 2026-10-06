from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import ProjectDep, SessionDep, UserDep
from app.api.schemas import SourceOut
from app.core.config import get_settings
from app.db.models import JobType, SourceFile
from app.services.jobs import create_job, dispatch
from app.services.sources import ALLOWED_EXTENSIONS, classify
from app.services.storage import get_storage, safe_filename

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])
MAX_FILES_PER_UPLOAD = 20


@router.get("", response_model=list[SourceOut])
async def list_sources(project: ProjectDep, session: SessionDep):
    rows = await session.scalars(
        select(SourceFile).where(SourceFile.project_id == project.id).order_by(SourceFile.created_at)
    )
    return rows.all()


@router.post("", response_model=list[SourceOut], status_code=status.HTTP_201_CREATED)
async def upload_sources(files: list[UploadFile], project: ProjectDep, user: UserDep, session: SessionDep):
    if not files or len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Upload 1-{MAX_FILES_PER_UPLOAD} files")
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    storage = get_storage()
    prepared = []
    for f in files:
        name = safe_filename(f.filename or "upload")
        kind = classify(name)
        if kind is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"'{name}' is not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )
        data = await f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"'{name}' exceeds the upload limit")
        if not data:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{name}' is empty")
        prepared.append((name, f.content_type, kind, data))

    created, jobs = [], []
    for name, content_type, kind, data in prepared:
        key = storage.new_key(f"{user.org_id}/{project.id}", name)
        storage.save(key, data)
        source = SourceFile(
            project_id=project.id,
            filename=name,
            content_type=content_type,
            size_bytes=len(data),
            storage_key=key,
            kind=kind,
        )
        session.add(source)
        await session.flush()
        jobs.append(
            await create_job(
                session,
                org_id=user.org_id,
                project_id=project.id,
                type=JobType.process_source,
                payload={"source_id": source.id},
                user_id=user.id,
            )
        )
        created.append(source)
    await session.commit()
    for job in jobs:
        await dispatch(job)
    return created


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: str, project: ProjectDep, session: SessionDep):
    source = await session.scalar(
        select(SourceFile).where(SourceFile.id == source_id, SourceFile.project_id == project.id)
    )
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    get_storage().delete(source.storage_key)
    await session.delete(source)
    await session.commit()
