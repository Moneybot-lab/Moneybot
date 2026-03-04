import pytest

from moneybot.app_factory import _resolve_database_url


def test_resolve_database_url_uses_postgres_internal_alias(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_INTERNAL_URL", "postgres://user:pw@localhost:5432/moneybot")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    resolved = _resolve_database_url()

    assert resolved.startswith("postgresql")


def test_resolve_database_url_rejects_sqlite_on_render(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_INTERNAL_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    monkeypatch.setenv("RENDER", "true")

    with pytest.raises(RuntimeError, match="No persistent PostgreSQL database"):
        _resolve_database_url()


def test_resolve_database_url_rejects_hosted_postgres_without_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost:5432/moneybot")
    monkeypatch.setenv("RENDER", "true")

    import importlib.util

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name):
        if name in {"psycopg", "psycopg2"}:
            return None
        return original_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(RuntimeError, match="no PostgreSQL driver is installed"):
        _resolve_database_url()
