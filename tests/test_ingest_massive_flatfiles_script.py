import json
from datetime import date

import pytest

from scripts.ingest_massive_flatfiles import (
    MANIFEST_NAME,
    build_subprocess_env,
    build_sync_command,
    dated_ingest_dir,
    ingest_massive_flatfiles,
)
from scripts.check_massive_flatfiles_env import load_massive_flatfiles_env


def _env():
    return {
        "MASSIVE_FLATFILES_ACCESS_KEY_ID": "access-key",
        "MASSIVE_FLATFILES_SECRET_ACCESS_KEY": "secret-key",
        "MASSIVE_FLATFILES_ENDPOINT": "https://files.massive.com",
        "MASSIVE_FLATFILES_BUCKET": "flatfiles",
    }


def test_dated_ingest_dir_sanitizes_prefix_into_immutable_date_tree(tmp_path):
    destination = dated_ingest_dir(tmp_path, "/us_stocks_sip/day aggs_v1/../2024/", date(2026, 7, 3))

    assert destination == tmp_path / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1" / "2024"


def test_ingest_massive_flatfiles_dry_run_writes_redacted_manifest(tmp_path):
    manifest_path = ingest_massive_flatfiles(
        prefix="us_stocks_sip/day_aggs_v1",
        destination_root=tmp_path,
        dataset_date=date(2026, 7, 3),
        env=_env(),
        dry_run=True,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path == tmp_path / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1" / MANIFEST_NAME
    assert manifest["schema_version"] == "massive-flatfiles-raw-ingest.v1"
    assert manifest["dataset_date"] == "2026-07-03"
    assert manifest["prefix"] == "us_stocks_sip/day_aggs_v1"
    assert manifest["credentials"] == {"access_key_id_configured": True, "secret_access_key_configured": True}
    assert "secret-key" not in manifest_path.read_text(encoding="utf-8")
    assert "access-key" not in manifest_path.read_text(encoding="utf-8")


def test_ingest_massive_flatfiles_refuses_existing_destination(tmp_path):
    existing = tmp_path / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    existing.mkdir(parents=True)
    (existing / "part.csv.gz").write_text("raw vendor bytes", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to overwrite immutable"):
        ingest_massive_flatfiles(
            prefix="us_stocks_sip/day_aggs_v1",
            destination_root=tmp_path,
            dataset_date=date(2026, 7, 3),
            env=_env(),
            dry_run=True,
        )


def test_build_sync_command_uses_massive_endpoint_bucket_and_no_secret_values(tmp_path):
    config = load_massive_flatfiles_env(_env())

    command = build_sync_command(config, prefix="us_stocks_sip/day_aggs_v1", destination=tmp_path)

    assert command == [
        "aws",
        "s3",
        "sync",
        "s3://flatfiles/us_stocks_sip/day_aggs_v1",
        str(tmp_path),
        "--endpoint-url",
        "https://files.massive.com",
        "--only-show-errors",
    ]
    assert "secret-key" not in " ".join(command)
    assert "access-key" not in " ".join(command)


def test_build_subprocess_env_maps_massive_secrets_to_aws_client_vars(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "old")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "old")

    child_env = build_subprocess_env(_env())

    assert child_env["AWS_ACCESS_KEY_ID"] == "access-key"
    assert child_env["AWS_SECRET_ACCESS_KEY"] == "secret-key"
