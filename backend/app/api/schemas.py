from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: str
    org_id: str
    org_name: str


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client_name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    target_platform: str = "salesforce"
    description: str = Field(default="", max_length=50_000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    target_platform: str | None = None
    description: str | None = Field(default=None, max_length=50_000)


class ProjectOut(ORM):
    id: str
    name: str
    client_name: str
    industry: str | None
    target_platform: str
    description: str
    status: str
    current_version: int | None
    created_at: datetime
    updated_at: datetime


class SourceOut(ORM):
    id: str
    filename: str
    content_type: str | None
    size_bytes: int
    kind: str
    status: str
    profile: dict | None
    error: str | None
    created_at: datetime


class QuestionOut(ORM):
    id: str
    key: str
    text: str
    why: str
    category: str
    priority: str
    suggested_answers: list[str]
    related: list[str]
    status: str
    answer: str | None
    answered_at: datetime | None
    asked_in_version: int | None
    applied_in_version: int | None


class QuestionPatch(BaseModel):
    answer: str | None = Field(default=None, max_length=10_000)
    status: Literal["answered", "dismissed", "open"] | None = None


class JobOut(ORM):
    id: str
    type: str
    status: str
    progress: str | None
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class VersionSummary(ORM):
    version: int
    source: str
    summary: str
    created_at: datetime
    error_count: int = 0
    warning_count: int = 0


class VersionOut(ORM):
    version: int
    source: str
    summary: str
    changes: list[str]
    issues: list[dict]
    model: dict
    llm_usage: dict | None
    created_at: datetime


class ModelUpdateIn(BaseModel):
    model: dict
    base_version: int = Field(description="Version the edit was based on, for optimistic concurrency.")
    note: str = Field(default="Manual edit", max_length=500)


class ExportIn(BaseModel):
    kind: str = Field(description="A platform key (e.g. 'salesforce') or 'design_doc'.")
    version: int | None = None


class ExportOut(ORM):
    id: str
    version: int
    kind: str
    filename: str
    report: dict
    created_at: datetime
