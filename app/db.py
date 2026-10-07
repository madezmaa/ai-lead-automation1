"""SQLAlchemy engine and session management.

The engine is configured lazily, so tests can point it at a throwaway
database before the application starts.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

_engine: Engine | None = None
_configured_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def configure_engine(url: str, *, echo: bool = False) -> Engine:
    """Create (or reuse) the engine bound to ``url``.

    Idempotent for the same URL; a different URL disposes the previous engine
    (which gives tests a fresh database per test).
    """
    global _configured_url, _engine, _session_factory
    if _engine is not None:
        if _configured_url == url:
            return _engine
        _engine.dispose()

    kwargs: dict[str, object] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool

    _engine = create_engine(url, echo=echo, pool_pre_ping=not url.startswith("sqlite"), **kwargs)
    _configured_url = url
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    """Return the configured engine, initializing it from settings if needed."""
    if _engine is None:
        configure_engine(get_settings().database_url)
    return _engine  # type: ignore[return-value]


def get_session_factory() -> sessionmaker[Session]:
    """Return the configured session factory."""
    if _session_factory is None:
        configure_engine(get_settings().database_url)
    return _session_factory  # type: ignore[return-value]


def create_schema() -> None:
    """Create all tables that do not yet exist (MVP bootstrap)."""
    from app.models import Base  # local import to avoid a circular import

    Base.metadata.create_all(get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
