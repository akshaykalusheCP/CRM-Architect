from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.adapters.registry import list_platforms
from app.api.deps import ProjectDep, SessionDep, UserDep
from app.api.schemas import JobOut, ProjectIn, ProjectOut, ProjectPatch
from app.db.models import Job, JobStatus, JobType, Project, ProjectStatus, SourceFile
from app.services.jobs import create_job, dispatch
from app.services.storage import get_storage

router = APIRouter(tags=["projects"])


def _check_platform(key: str) -> None:
    if key not in {p["key"] for p in list_platforms()}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown platform '{key}'")


@router.get("/platforms")
async def platforms(_: UserDep):
    return list_platforms()


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(user: UserDep, session: SessionDep):
    rows = await session.scalars(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.updated_at.desc())
    )
    return rows.all()


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectIn, user: UserDep, session: SessionDep):
    _check_platform(body.target_platform)
    project = Project(org_id=user.org_id, created_by=user.id, **body.model_dump())
    session.add(project)
    await session.commit()
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project: ProjectDep):
    return project


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(body: ProjectPatch, project: ProjectDep, session: SessionDep):
    data = body.model_dump(exclude_unset=True)
    if "target_platform" in data:
        _check_platform(data["target_platform"])
    for k, v in data.items():
        setattr(project, k, v)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project: ProjectDep, session: SessionDep):
    storage = get_storage()
    for s in (await session.scalars(select(SourceFile).where(SourceFile.project_id == project.id))).all():
        storage.delete(s.storage_key)
    await session.delete(project)
    await session.commit()


@router.post("/projects/{project_id}/analyze", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def analyze(project: ProjectDep, user: UserDep, session: SessionDep):
    # Jobs older than the worker timeout are treated as dead (e.g. the worker crashed mid-run).
    stale_before = datetime.now(UTC) - timedelta(minutes=45)
    running = await session.scalar(
        select(Job).where(
            Job.project_id == project.id,
            Job.type == JobType.analyze,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.created_at > stale_before,
        )
    )
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "An analysis is already running for this project")
    job = await create_job(
        session, org_id=user.org_id, project_id=project.id, type=JobType.analyze, payload={}, user_id=user.id
    )
    project.status = ProjectStatus.analyzing
    await session.commit()
    await dispatch(job)
    return job


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
async def list_jobs(project: ProjectDep, session: SessionDep, limit: int = 20):
    rows = await session.scalars(
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(min(limit, 100))
    )
    return rows.all()


@router.get("/projects/{project_id}/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, project: ProjectDep, session: SessionDep):
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.project_id == project.id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
