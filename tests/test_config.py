"""Database URL resolution and normalization for managed providers."""

from app.config import (
    DATABASE_URL_ENV_VARS,
    DEFAULT_DATABASE_URL,
    Settings,
    describe_database_url,
)


def _url(value: str) -> str:
    return Settings(database_url=value).database_url


def _clear_db_env(monkeypatch) -> None:
    for name in DATABASE_URL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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


def test_local_default_when_nothing_configured(monkeypatch):
    _clear_db_env(monkeypatch)
    assert Settings(_env_file=None).database_url == DEFAULT_DATABASE_URL


def test_alias_env_var_used_when_database_url_absent(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@db.host:5432/leads")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://u:p@db.host:5432/leads"


def test_primary_env_var_wins_over_alias(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://alt@alt.host/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary@primary.host/db")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://primary@primary.host/db"


def test_blank_database_url_falls_back_to_alias(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://real@real.host/db")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://real@real.host/db"


def test_describe_database_url_hides_password_and_shows_target():
    described = describe_database_url("postgresql+psycopg://user:s3cret@db.example.com:5432/leads")
    assert "s3cret" not in described
    assert "db.example.com" in described
    assert "5432" in described
    assert "leads" in described
