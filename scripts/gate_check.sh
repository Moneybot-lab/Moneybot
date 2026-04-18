#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 50_to_75
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 75_to_100

Options:
  --gate <50_to_75|75_to_100>   Promotion gate profile (required)
  --limit <n>                   decision-outcomes limit (default: 100)

Environment:
  BASE_URL                      API base URL (required)
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

gate=""
limit="100"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gate)
      gate="${2:-}"
      shift 2
      ;;
    --limit)
      limit="${2:-100}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${BASE_URL:-}" ]]; then
  echo "BASE_URL is required." >&2
  usage
  exit 2
fi

if [[ "$gate" != "50_to_75" && "$gate" != "75_to_100" ]]; then
  echo "--gate must be one of: 50_to_75, 75_to_100" >&2
  usage
  exit 2
fi

require_cmd curl
require_cmd jq

model_json="$(curl -fsS "${BASE_URL}/api/model-health")"
outcomes_json="$(curl -fsS "${BASE_URL}/api/decision-outcomes?limit=${limit}&force_live=true")"

if ! echo "$model_json" | jq -e '.data' >/dev/null; then
  echo "model-health response missing .data" >&2
  exit 2
fi
if ! echo "$outcomes_json" | jq -e '.data' >/dev/null; then
  echo "decision-outcomes response missing .data" >&2
  exit 2
fi

pass_count=0
fail_count=0

check_bool() {
  local label="$1"
  local expr="$2"
  if jq -e "$expr" >/dev/null 2>&1; then
    printf "PASS  %s\n" "$label"
    pass_count=$((pass_count + 1))
  else
    printf "FAIL  %s\n" "$label"
    fail_count=$((fail_count + 1))
  fi
}

check_bool_with_json() {
  local label="$1"
  local expr="$2"
  local json="$3"
  if echo "$json" | jq -e "$expr" >/dev/null 2>&1; then
    printf "PASS  %s\n" "$label"
    pass_count=$((pass_count + 1))
  else
    printf "FAIL  %s\n" "$label"
    fail_count=$((fail_count + 1))
  fi
}

echo "Gate profile: $gate"
echo "BASE_URL: $BASE_URL"
echo

# Common checks
check_bool_with_json "model_loaded == true" '.data.model_loaded == true' "$model_json"
check_bool_with_json "model_load_error is empty" '((.data.model_load_error // "") | tostring | length) == 0' "$model_json"
check_bool_with_json "decision_logging.enabled == true" '.data.decision_logging.enabled == true' "$model_json"
check_bool_with_json "used_unevaluated_fallback == false" '.data.used_unevaluated_fallback == false' "$outcomes_json"
check_bool_with_json "lookup_errors == 0" '(.data.lookup_errors // 0) == 0' "$outcomes_json"

if [[ "$gate" == "50_to_75" ]]; then
  check_bool_with_json "summary_5d.evaluated_rows >= 20" '(.data.summary_5d.evaluated_rows // 0) >= 20' "$outcomes_json"
  check_bool_with_json "summary_5d.accuracy >= 0.52" '(.data.summary_5d.accuracy // 0) >= 0.52' "$outcomes_json"
  check_bool_with_json "summary_1d.evaluated_rows >= 40" '(.data.summary_1d.evaluated_rows // 0) >= 40' "$outcomes_json"
  check_bool_with_json "summary_1d.accuracy >= 0.48" '(.data.summary_1d.accuracy // 0) >= 0.48' "$outcomes_json"
  check_bool_with_json "calibration_report.rows >= 30" '(.data.calibration_report.rows // 0) >= 30' "$model_json"
  check_bool_with_json "calibration_report.brier_score <= 0.26" '((.data.calibration_report.brier_score // 999) <= 0.26)' "$model_json"
else
  check_bool_with_json "summary_5d.evaluated_rows >= 60" '(.data.summary_5d.evaluated_rows // 0) >= 60' "$outcomes_json"
  check_bool_with_json "summary_5d.accuracy >= 0.55" '(.data.summary_5d.accuracy // 0) >= 0.55' "$outcomes_json"
  check_bool_with_json "summary_1d.evaluated_rows >= 100" '(.data.summary_1d.evaluated_rows // 0) >= 100' "$outcomes_json"
  check_bool_with_json "summary_1d.accuracy >= 0.50" '(.data.summary_1d.accuracy // 0) >= 0.50' "$outcomes_json"
  check_bool_with_json "calibration_report.rows >= 100" '(.data.calibration_report.rows // 0) >= 100' "$model_json"
  check_bool_with_json "calibration_report.brier_score <= 0.24" '((.data.calibration_report.brier_score // 999) <= 0.24)' "$model_json"
fi

echo
echo "Summary: PASS=$pass_count FAIL=$fail_count"
echo
echo "Observed metrics:"
echo "$outcomes_json" | jq '.data | {summary_1d, summary_5d, used_unevaluated_fallback, lookup_errors}'
echo "$model_json" | jq '.data | {model_loaded, model_load_error, decision_logging, calibration_report: (.calibration_report // {rows:0,brier_score:null})}'

if [[ $fail_count -gt 0 ]]; then
  exit 1
fi

