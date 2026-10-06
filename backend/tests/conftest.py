import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="crm-architect-test-"))
os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
        "DATA_DIR": str(_tmp / "data"),
        "JOB_BACKEND": "inline",
        "LLM_PROVIDER": "mock",
        "SECRET_KEY": "test-secret-key-0123456789-abcdefghij",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.db import dispose_engine, get_engine  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
async def client():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await dispose_engine()


@pytest.fixture
async def auth_client(client):
    r = await client.post(
        "/api/auth/register",
        json={
            "org_name": "Acme Consulting",
            "full_name": "Test User",
            "email": "consultant@example.com",
            "password": "correct-horse-battery",
        },
    )
    assert r.status_code == 201, r.text
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    return client
