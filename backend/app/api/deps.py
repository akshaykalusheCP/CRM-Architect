from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, decode_access_token
from app.db.models import Project, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def current_user(
    request: Request,
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token, via_cookie = None, False
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    elif access_token:
        token, via_cookie = access_token, True
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from exc

    # Cookie auth is vulnerable to CSRF; require the double-submit token on state-changing requests.
    if via_cookie and request.method not in SAFE_METHODS:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if not cookie_token or request.headers.get(CSRF_HEADER) != cookie_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")

    user = await session.get(User, payload["sub"])
    if user is None or user.org_id != payload.get("org"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


UserDep = Annotated[User, Depends(current_user)]


async def get_project(project_id: str, user: UserDep, session: SessionDep) -> Project:
    project = await session.scalar(select(Project).where(Project.id == project_id, Project.org_id == user.org_id))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


ProjectDep = Annotated[Project, Depends(get_project)]
