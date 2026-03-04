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
