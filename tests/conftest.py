"""Shared pytest fixtures.

Tests run against a throwaway SQLite file by default. Set ``TEST_DATABASE_URL``
(e.g. ``postgresql+psycopg://lead:lead@localhost:5432/leads_test``) to run the
same suite against a real PostgreSQL server; the schema is dropped and
recreated for every test.
"""

from __future__ import annotations

import itertools
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import configure_engine
from app.main import create_app
from app.models import Base

_counter = itertools.count()


def make_settings(**overrides) -> Settings:
    """Build Settings isolated from any local .env file."""
    defaults: dict = {
        "_env_file": None,
        "auto_create_schema": True,
        "ollama_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _database_url(tmp_path, name: str) -> str:
    shared = os.environ.get("TEST_DATABASE_URL")
    if shared:
        return shared
    return f"sqlite:///{tmp_path.as_posix()}/{name}.db"


def _reset_schema(url: str):
    engine = configure_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return make_settings(database_url=_database_url(tmp_path, "test"))


@pytest.fixture()
def app_instance(settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture()
def schema_engine(settings):
    return _reset_schema(settings.database_url)


@pytest.fixture()
def client(app_instance, schema_engine) -> TestClient:
    with TestClient(app_instance) as test_client:
        yield test_client


@pytest.fixture()
def test_client_factory(tmp_path):
    """Build a TestClient with arbitrary Settings overrides."""

    def _build(client_kwargs: dict | None = None, **overrides) -> TestClient:
        overrides.setdefault("database_url", _database_url(tmp_path, f"client_{next(_counter)}"))
        _reset_schema(overrides["database_url"])
        app = create_app(make_settings(**overrides))
        return TestClient(app, **(client_kwargs or {}))

    return _build


@pytest.fixture()
def db_session(schema_engine):
    session_factory = sessionmaker(bind=schema_engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def strong_payload() -> dict:
    """A lead the deterministic rules will score as clearly qualified."""
    return {
        "email": "Alex.Johnson@ACME.io  ",
        "first_name": " alex ",
        "last_name": "johnson",
        "company": "Acme Inc",
        "job_title": "VP Sales",
        "phone": "(415) 555-2671",
        "website": "acme.io",
        "industry": "saas",
        "country": "USA",
        "source": "referral",
        "message": "Need a demo and pricing for a Q3 rollout of your platform.",
        "budget": 20000,
        "company_size": 250,
    }


@pytest.fixture()
def weak_payload() -> dict:
    """A lead the deterministic rules will score as disqualified."""
    return {
        "email": "jane.smith@gmail.com",
        "first_name": "jane",
        "last_name": "smith",
        "job_title": "Support Specialist",
        "industry": "retail",
        "country": "Mordor",
        "source": "website",
        "message": "Saw your website.",
        "company_size": 2,
    }


@pytest.fixture()
def mid_payload() -> dict:
    """A lead the rules will score into the nurture band."""
    return {
        "email": "mike@midco.de",
        "company": "MidCo",
        "industry": "software",
        "country": "Germany",
        "company_size": 50,
        "budget": 1500,
        "source": "inbound",
    }
