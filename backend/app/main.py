import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth, exports, knowledge, knowledge_base, llm_settings, model, projects, questions, sources
from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import configure_logging
from app.services.jobs import close_arq_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    get_engine()
    yield
    await close_arq_pool()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.get(f"{settings.api_prefix}/health")
    async def health():
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    for r in (
        auth.router,
        projects.router,
        sources.router,
        questions.router,
        model.router,
        knowledge.router,
        knowledge_base.router,
        exports.router,
        llm_settings.router,
    ):
        app.include_router(r, prefix=settings.api_prefix)
    return app


app = create_app()
