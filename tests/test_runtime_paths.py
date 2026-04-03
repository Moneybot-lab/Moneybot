from __future__ import annotations

from pathlib import Path

from moneybot.services.runtime_paths import (
    decision_events_log_path,
    decision_outcomes_snapshot_path,
    is_durable_runtime_configured,
    resolve_runtime_dir,
)


def test_runtime_paths_use_persistent_data_dir(monkeypatch, tmp_path):
    persistent = tmp_path / "persistent-runtime"
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(persistent))
    monkeypatch.delenv("MONEYBOT_RUNTIME_DIR", raising=False)

    resolved = resolve_runtime_dir()
    assert resolved == persistent
    assert resolved.exists()
    assert is_durable_runtime_configured() is True
    assert decision_events_log_path() == persistent / "decision_events.jsonl"


def test_runtime_paths_fall_back_to_runtime_dir(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime-only"
    monkeypatch.delenv("MONEYBOT_PERSISTENT_DATA_DIR", raising=False)
    monkeypatch.setenv("MONEYBOT_RUNTIME_DIR", str(runtime))

    resolved = resolve_runtime_dir()
    assert resolved == runtime
    assert resolved.exists()
    assert is_durable_runtime_configured() is True
    assert decision_outcomes_snapshot_path() == runtime / "decision_outcomes_snapshot.json"


def test_runtime_paths_default_to_local_data(monkeypatch):
    monkeypatch.delenv("MONEYBOT_PERSISTENT_DATA_DIR", raising=False)
    monkeypatch.delenv("MONEYBOT_RUNTIME_DIR", raising=False)

    resolved = resolve_runtime_dir()
    assert resolved == Path("data")
    assert is_durable_runtime_configured() is False


def test_runtime_paths_fallback_to_local_when_configured_path_unwritable(monkeypatch):
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", "/var/data/moneybot")
    monkeypatch.delenv("MONEYBOT_RUNTIME_DIR", raising=False)

    original_mkdir = Path.mkdir

    def _fake_mkdir(self, *args, **kwargs):  # noqa: ANN001, ANN002
        if str(self).startswith("/var/data"):
            raise PermissionError("permission denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)

    resolved = resolve_runtime_dir()
    assert resolved == Path("data")
