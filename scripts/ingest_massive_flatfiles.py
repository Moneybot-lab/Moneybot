#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from scripts.check_massive_flatfiles_env import MassiveFlatFilesEnv, load_massive_flatfiles_env

MANIFEST_NAME = "_INGEST_MANIFEST.json"
DEFAULT_RAW_INGEST_ROOT = Path("data/raw/massive_flatfiles")
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._=-]+")


def _required_secret(env: Mapping[str, str], key: str) -> str:
    value = str(env.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def parse_dataset_date(value: str | None = None) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(value)


def sanitize_prefix(prefix: str) -> Path:
    clean_parts: list[str] = []
    for raw_part in prefix.strip().strip("/").split("/"):
        if raw_part in {"", ".", ".."}:
            continue
        clean_part = _SAFE_SEGMENT.sub("_", raw_part).strip("._")
        if clean_part:
            clean_parts.append(clean_part)
    if not clean_parts:
        raise ValueError("prefix must include at least one safe path segment")
    return Path(*clean_parts)


def dated_ingest_dir(destination_root: Path, prefix: str, dataset_date: date) -> Path:
    return destination_root / dataset_date.isoformat() / sanitize_prefix(prefix)


def assert_immutable_destination(destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(
            f"Refusing to overwrite immutable Massive flat-file ingest folder: {destination}. "
            "Choose a new --dataset-date or a different --destination-root."
        )


def build_sync_command(config: MassiveFlatFilesEnv, *, prefix: str, destination: Path) -> list[str]:
    source = f"s3://{config.bucket}/{prefix.strip('/')}"
    return ["aws", "s3", "sync", source, str(destination), "--endpoint-url", config.endpoint, "--only-show-errors"]


def build_subprocess_env(env: Mapping[str, str]) -> dict[str, str]:
    child_env = dict(os.environ)
    child_env["AWS_ACCESS_KEY_ID"] = _required_secret(env, "MASSIVE_FLATFILES_ACCESS_KEY_ID")
    child_env["AWS_SECRET_ACCESS_KEY"] = _required_secret(env, "MASSIVE_FLATFILES_SECRET_ACCESS_KEY")
    return child_env


def write_manifest(destination: Path, *, config: MassiveFlatFilesEnv, prefix: str, dataset_date: date, command: Sequence[str], dry_run: bool) -> Path:
    manifest = {
        "schema_version": "massive-flatfiles-raw-ingest.v1",
        "dataset_date": dataset_date.isoformat(),
        "bucket": config.bucket,
        "endpoint": config.endpoint,
        "prefix": prefix.strip("/"),
        "destination": str(destination),
        "dry_run": dry_run,
        "command": list(command),
        "credentials": {
            "access_key_id_configured": config.access_key_id_configured,
            "secret_access_key_configured": config.secret_access_key_configured,
        },
    }
    path = destination / MANIFEST_NAME
    destination.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def ingest_massive_flatfiles(
    *,
    prefix: str,
    destination_root: Path = DEFAULT_RAW_INGEST_ROOT,
    dataset_date: date | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> Path:
    source_env = os.environ if env is None else env
    config = load_massive_flatfiles_env(source_env)
    if not config.ready:
        raise RuntimeError(f"Massive flat-file environment is incomplete: {', '.join(config.missing)}")

    ingest_date = dataset_date or parse_dataset_date()
    destination = dated_ingest_dir(destination_root, prefix, ingest_date)
    assert_immutable_destination(destination)
    command = build_sync_command(config, prefix=prefix, destination=destination)

    if not dry_run:
        destination.mkdir(parents=True, exist_ok=False)
        subprocess.run(command, check=True, env=build_subprocess_env(source_env))

    return write_manifest(destination, config=config, prefix=prefix, dataset_date=ingest_date, command=command, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Massive raw flat files into immutable dated folders.")
    parser.add_argument("--prefix", required=True, help="Massive flat-files bucket prefix, for example us_stocks_sip/day_aggs_v1.")
    parser.add_argument("--dataset-date", help="UTC dataset date folder to write, in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--destination-root", type=Path, default=DEFAULT_RAW_INGEST_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Create only the manifest and do not run aws s3 sync.")
    args = parser.parse_args()

    manifest_path = ingest_massive_flatfiles(
        prefix=args.prefix,
        destination_root=args.destination_root,
        dataset_date=parse_dataset_date(args.dataset_date),
        dry_run=args.dry_run,
    )
    print(json.dumps({"manifest": str(manifest_path)}, sort_keys=True))


if __name__ == "__main__":
    main()
