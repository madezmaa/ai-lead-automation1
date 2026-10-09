"""FastAPI application factory and wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, get_settings
from app.db import configure_engine, create_schema
from app.errors import DuplicateLeadError, LeadNotFoundError, NotQualifiedError
from app.routers import health, leads
from app.state_machine import InvalidTransitionError


def configure_logging(level: str) -> None:
    """Configure root logging once, honouring ``LOG_LEVEL``."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _json_error(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": message, "code": code},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. The database engine is configured lazily on startup."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_engine(resolved.database_url)
        if resolved.auto_create_schema:
            create_schema()
        yield

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        description=(
            "AI Lead Qualification & CRM Automation. Deterministic rules are "
            "always applied; an optional Ollama layer adds AI judgment with an "
            "automatic fallback to the rules whenever the model is unavailable."
        ),
        lifespan=lifespan,
        servers=None,
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(leads.router)

    # Optionally serve the static demo from the same origin so a single public
    # deployment exposes both the API and the demo (no CORS / separate host).
    demo_dir = Path(__file__).resolve().parent.parent / "demo"
    if demo_dir.is_dir():
        app.mount("/demo", StaticFiles(directory=str(demo_dir), html=True), name="demo")

        @app.get("/", include_in_schema=False)
        async def _root() -> RedirectResponse:
            return RedirectResponse(url="/demo/")

    register_exception_handlers(app)
    return app


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LeadNotFoundError)
    async def on_lead_not_found(_: Request, exc: LeadNotFoundError) -> JSONResponse:
        return _json_error(404, str(exc), "lead_not_found")

    @app.exception_handler(DuplicateLeadError)
    async def on_duplicate(_: Request, exc: DuplicateLeadError) -> JSONResponse:
        return _json_error(409, str(exc), "duplicate_lead")

    @app.exception_handler(NotQualifiedError)
    async def on_not_qualified(_: Request, exc: NotQualifiedError) -> JSONResponse:
        return _json_error(409, str(exc), "not_qualified")

    @app.exception_handler(InvalidTransitionError)
    async def on_invalid_transition(_: Request, exc: InvalidTransitionError) -> JSONResponse:
        return _json_error(409, str(exc), "invalid_transition")


app = create_app()
