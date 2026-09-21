#!/usr/bin/env python3
"""Render corrected KAII findings and a support packet from preserved evaluation bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import zipfile

from scripts.evaluate_kaii_trade_conditions import _markdown
from scripts.inspect_historical_coverage_evidence import _safe_member, MAX_MEMBERS, MAX_TOTAL_BYTES

EVALUATION_RUN = 35255222521
EVALUATION_ATTEMPT = 1
EVALUATION_HEAD_SHA = "bed6a95b0b4fafa9f8fd18a8c9e33346fc83fddc"
EVALUATION_ARTIFACT = "kaii-trade-condition-evaluation-35255222521-1"
EVALUATION_ARTIFACT_ID = 10511084736
EVALUATION_ARTIFACT_DIGEST = "sha256:cd734e934fea432c65f6dda64df40e82bbd4817bc37f1ab488ecfa269e5d68cb"
REPORT_MEMBERS = ("derived/kaii_trade_condition_evaluation.json",
                  "derived/kaii_trade_condition_evaluation.md")


def extract_reports(run: dict, listing: dict, archive_path: Path, output_dir: Path, *,
                    expected_artifact_digest: str = EVALUATION_ARTIFACT_DIGEST) -> tuple[Path, Path]:
    expected = {"id": EVALUATION_RUN, "run_attempt": EVALUATION_ATTEMPT,
                "head_sha": EVALUATION_HEAD_SHA, "conclusion": "success",
                "name": "Evaluate KAII Trade Conditions"}
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError("EVALUATION_RUN_METADATA_MISMATCH")
    if (run.get("repository") or {}).get("full_name") != "Moneybot-lab/Moneybot":
        raise ValueError("EVALUATION_REPOSITORY_MISMATCH")
    matches = [row for row in listing.get("artifacts", []) if row.get("id") == EVALUATION_ARTIFACT_ID]
    if len(matches) != 1 or matches[0].get("name") != EVALUATION_ARTIFACT:
        raise ValueError("EVALUATION_ARTIFACT_IDENTITY_MISMATCH")
    if matches[0].get("expired") is not False or matches[0].get("digest") != expected_artifact_digest:
        raise ValueError("EVALUATION_ARTIFACT_STATE_OR_DIGEST_MISMATCH")
    digest = "sha256:" + hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != expected_artifact_digest:
        raise ValueError("EVALUATION_ARCHIVE_DIGEST_MISMATCH")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS or sum(row.file_size for row in members) > MAX_TOTAL_BYTES:
            raise ValueError("EVALUATION_ARCHIVE_BUDGET_EXCEEDED")
        seen = set()
        for row in members:
            _safe_member(row)
            normalized = str(PurePosixPath(row.filename)).casefold()
            if normalized in seen:
                raise ValueError("EVALUATION_ARCHIVE_DUPLICATE_MEMBER")
            seen.add(normalized)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in REPORT_MEMBERS:
            exact = [row for row in members if row.filename == name and not row.is_dir()]
            if len(exact) != 1:
                raise ValueError(f"EVALUATION_REPORT_MISSING_OR_AMBIGUOUS:{name}")
            path = output_dir / Path(name).name
            path.write_bytes(archive.read(exact[0])); paths.append(path)
    return paths[0], paths[1]


def _provenance(result: dict) -> list[dict]:
    allowed = {"url", "method", "status", "http_status", "response_bytes", "response_sha256", "request_id",
               "x_request_id", "page", "records_received", "next_url_present"}
    return [{key: row[key] for key in allowed if key in row}
            for row in result.get("request_provenance", []) if isinstance(row, dict)]


def build_packet(report: dict, *, evaluation_json_sha256: str,
                 evaluation_markdown_sha256: str) -> tuple[str, dict, str]:
    corrected = _markdown(report)
    assessments = {row["date"]: row for row in report["february_assessments"]}
    technical = {
        "schema_version": "alpha-atlas-v4-kaii-support-evidence.v1",
        "source_evaluation": {"run_id": EVALUATION_RUN, "run_attempt": EVALUATION_ATTEMPT,
            "head_sha": EVALUATION_HEAD_SHA, "artifact": EVALUATION_ARTIFACT,
            "evaluation_json_sha256": evaluation_json_sha256,
            "evaluation_markdown_sha256": evaluation_markdown_sha256,
            "source_diagnostic": report.get("source")},
        "security": {"historical_ticker": "KAII", "class": "Class A ordinary shares",
            "identity_context": "KAII changed to QDRO effective 2023-02-27; QDRO is not substitute price evidence."},
        "january_19": {"scope": report["january_19"]["scope"],
            "trade_records": report["january_19"]["trade_records"],
            "historical_rule_applicability": "UNVERIFIED/UNKNOWN"},
        "february": [],
    }
    for day in ("2023-02-17", "2023-02-24"):
        item = assessments[day]; result = item["additional_query_result"]
        query = item["smallest_additional_query"]
        technical["february"].append({"date": day, "regular_session": item["saved_evidence"],
            "full_day_query": {"endpoint": query["endpoint"], "timestamp_gte": query["timestamp_gte"],
                "timestamp_lt": query["timestamp_lt"], "page_limit": query["page_limit"],
                "record_limit": query["record_limit"]},
            "full_day_result": {"status": result.get("status"), "http_status": result.get("http_status"),
                "pagination_complete": result.get("pagination_complete"),
                "returned_count": result.get("records_observed", len(result.get("records") or [])),
                "sanitized_request_url": result.get("initial_request_url"),
                "records": result.get("records") or [], "provenance": _provenance(result)},
            "condition_evaluation": item.get("additional_trade_condition_evaluation") or [],
            "historical_rule_applicability": "UNVERIFIED/UNKNOWN"})
    message = """Subject: Historical KAII trade coverage and aggregate-condition questions (January–February 2023)

Hello Massive Support,

We are reviewing bounded historical evidence for KAII Class A ordinary shares (renamed QDRO effective February 27, 2023; we are not using QDRO as substitute price evidence). Regular-session queries found two KAII trades on January 19 with conditions [17,37,41] and [16], but no daily/minute aggregate. Full-Eastern-calendar-day queries found two extended-hours odd-lot trades on February 17 (2 shares at $10.19 and 3 shares at $10.20; conditions [14,12,37,41]) and returned zero trades with complete pagination on February 24. The attached technical evidence contains sanitized URLs, UTC bounds, statuses, counts, hashes, and request IDs where the API supplied them.

Could you clarify: (1) whether February 24's response is complete supported SIP coverage or subject to retention, entitlement, correction, ticker-mapping, or coverage limitations; (2) whether empty means no trades in covered data and how that differs from missing coverage; (3) which timestamp controls filtering and whether late reports/corrections can fall outside these bounds; (4) whether today's condition definitions and NO-precedence rule govern historical aggregates currently served for 2023, separately from rules applied in 2023; (5) whether these condition combinations are expected to produce no consolidated daily/minute OHLC when volume may still update; and (6) any relevant documentation, historical rule version, or data-quality issue ID?

We are not alleging a provider error and are not requesting an entitlement upgrade. Thank you.
"""
    return corrected, technical, message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-json", type=Path)
    parser.add_argument("--evaluation-markdown", type=Path)
    parser.add_argument("--run-metadata", type=Path)
    parser.add_argument("--artifact-metadata", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--expected-artifact-digest", default=EVALUATION_ARTIFACT_DIGEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    evaluation_json, evaluation_markdown = args.evaluation_json, args.evaluation_markdown
    if args.archive:
        if not args.run_metadata or not args.artifact_metadata:
            parser.error("--archive requires --run-metadata and --artifact-metadata")
        evaluation_json, evaluation_markdown = extract_reports(
            json.loads(args.run_metadata.read_text()), json.loads(args.artifact_metadata.read_text()),
            args.archive, args.output_dir / "preserved-reports",
            expected_artifact_digest=args.expected_artifact_digest)
    if not evaluation_json or not evaluation_markdown:
        parser.error("provide report paths or the validated artifact inputs")
    json_bytes = evaluation_json.read_bytes(); markdown_bytes = evaluation_markdown.read_bytes()
    report = json.loads(json_bytes)
    corrected, technical, message = build_packet(report,
        evaluation_json_sha256=hashlib.sha256(json_bytes).hexdigest(),
        evaluation_markdown_sha256=hashlib.sha256(markdown_bytes).hexdigest())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "kaii_trade_condition_evaluation_corrected.md").write_text(corrected)
    (args.output_dir / "massive_support_evidence.json").write_text(json.dumps(technical, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "massive_support_inquiry.md").write_text(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
