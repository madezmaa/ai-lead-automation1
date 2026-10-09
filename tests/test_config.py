"""Database URL normalization for managed providers (Render/Supabase)."""

from app.config import Settings


def _url(value: str) -> str:
    return Settings(database_url=value).database_url


def test_bare_postgres_scheme_gets_psycopg_driver():
    assert _url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"


def test_bare_postgresql_scheme_gets_psycopg_driver():
    assert _url("postgresql://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"


def test_already_qualified_url_is_unchanged():
    url = "postgresql+psycopg://u:p@h:5432/db"
    assert _url(url) == url


def test_sqlite_url_is_unchanged(tmp_path):
    url = f"sqlite:///{tmp_path / 'x.db'}"
    assert _url(url) == url
