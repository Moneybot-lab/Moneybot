#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Mapping

DEFAULT_MASSIVE_FLATFILES_ENDPOINT = "https://files.massive.com"
DEFAULT_MASSIVE_FLATFILES_BUCKET = "flatfiles"


@dataclass(frozen=True)
class MassiveFlatFilesEnv:
    access_key_id_configured: bool
    secret_access_key_configured: bool
    endpoint: str
    bucket: str
    ready: bool
    missing: list[str]


def _env_value(env: Mapping[str, str], key: str, default: str | None = None) -> str:
    value = env.get(key, default or "")
    return str(value or "").strip()


def load_massive_flatfiles_env(env: Mapping[str, str] | None = None) -> MassiveFlatFilesEnv:
    source = os.environ if env is None else env
    access_key_id = _env_value(source, "MASSIVE_FLATFILES_ACCESS_KEY_ID")
    secret_access_key = _env_value(source, "MASSIVE_FLATFILES_SECRET_ACCESS_KEY")
    endpoint = _env_value(source, "MASSIVE_FLATFILES_ENDPOINT", DEFAULT_MASSIVE_FLATFILES_ENDPOINT)
    bucket = _env_value(source, "MASSIVE_FLATFILES_BUCKET", DEFAULT_MASSIVE_FLATFILES_BUCKET)

    missing: list[str] = []
    if not access_key_id:
        missing.append("MASSIVE_FLATFILES_ACCESS_KEY_ID")
    if not secret_access_key:
        missing.append("MASSIVE_FLATFILES_SECRET_ACCESS_KEY")
    if not endpoint:
        missing.append("MASSIVE_FLATFILES_ENDPOINT")
    if not bucket:
        missing.append("MASSIVE_FLATFILES_BUCKET")

    return MassiveFlatFilesEnv(
        access_key_id_configured=bool(access_key_id),
        secret_access_key_configured=bool(secret_access_key),
        endpoint=endpoint,
        bucket=bucket,
        ready=not missing,
        missing=missing,
    )


def build_aws_cli_command(config: MassiveFlatFilesEnv, *, prefix: str = "", destination: str = "data/massive_flatfiles") -> list[str]:
    source = f"s3://{config.bucket}/{prefix.lstrip('/')}" if prefix else f"s3://{config.bucket}/"
    return ["aws", "s3", "sync", source, destination, "--endpoint-url", config.endpoint]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Massive flat-files environment variables without printing secrets.")
    parser.add_argument("--prefix", default="", help="Optional bucket prefix to include in the sample aws s3 sync command.")
    parser.add_argument("--destination", default="data/massive_flatfiles", help="Local destination for the sample aws s3 sync command.")
    args = parser.parse_args()

    config = load_massive_flatfiles_env()
    payload = asdict(config)
    payload["sample_sync_command"] = build_aws_cli_command(config, prefix=args.prefix, destination=args.destination)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not config.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
