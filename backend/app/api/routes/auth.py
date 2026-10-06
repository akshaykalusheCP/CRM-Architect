from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.api.deps import SessionDep, UserDep
from app.api.schemas import LoginIn, RegisterIn, UserOut
from app.core.config import get_settings
from app.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    create_access_token,
    hash_password,
    new_csrf_token,
    verify_password,
)
from app.db.models import Organization, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        org_id=user.org_id,
        org_name=user.organization.name,
    )


def _set_session(response: Response, user: User) -> None:
    settings = get_settings()
    max_age = settings.access_token_ttl_minutes * 60
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(user.id, user.org_id),
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, response: Response, session: SessionDep):
    email = body.email.lower()
    if await session.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    org = Organization(name=body.org_name)
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id,
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=UserRole.owner,
    )
    user.organization = org
    session.add(user)
    await session.commit()
    _set_session(response, user)
    return _user_out(user)


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, response: Response, session: SessionDep):
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    # Hash even for unknown emails so response timing does not reveal which accounts exist.
    password_hash = user.password_hash if user else _DUMMY_HASH
    if not verify_password(body.password, password_hash) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _set_session(response, user)
    return _user_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: UserDep):
    return _user_out(user)
