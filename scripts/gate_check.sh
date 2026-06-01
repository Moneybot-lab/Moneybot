#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 50_to_75
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 75_to_100
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_20_to_35
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_35_to_50
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_50_to_75
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_75_to_100

Options:
  --gate <50_to_75|75_to_100|portfolio_20_to_35|portfolio_35_to_50|portfolio_50_to_75|portfolio_75_to_100>
                                 Promotion gate profile (required)
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

if [[ "$gate" != "50_to_75" && "$gate" != "75_to_100" && "$gate" != "portfolio_20_to_35" && "$gate" != "portfolio_35_to_50" && "$gate" != "portfolio_50_to_75" && "$gate" != "portfolio_75_to_100" ]]; then
  echo "--gate must be one of: 50_to_75, 75_to_100, portfolio_20_to_35, portfolio_35_to_50, portfolio_50_to_75, portfolio_75_to_100" >&2
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

combined_json="$(jq -n --argjson outcomes "$outcomes_json" --argjson model "$model_json" '{outcomes: $outcomes.data, model: $model.data}')"
five_day_evidence_expr() {
  local min_rows="$1"
  printf '((.outcomes.summary_5d.evaluated_rows // .outcomes.evaluated_rows_5d_available // 0) >= %s) or ((.model.calibration_report.rows // 0) >= %s)' "$min_rows" "$min_rows"
}

if [[ "$gate" == "50_to_75" ]]; then
  check_bool_with_json "5d evidence rows >= 20" "$(five_day_evidence_expr 20)" "$combined_json"
  check_bool_with_json "evaluated_rows_available >= 40" '(.data.evaluated_rows_available // (.data.summary_1d.evaluated_rows // 0)) >= 40' "$outcomes_json"
  check_bool_with_json "summary_1d.accuracy >= 0.48" '(.data.summary_1d.accuracy // 0) >= 0.48' "$outcomes_json"
  check_bool_with_json "calibration_report.rows >= 30" '(.data.calibration_report.rows // 0) >= 30' "$model_json"
  check_bool_with_json "calibration_report.effective_brier_score <= 0.26" '((.data.calibration_report.effective_brier_score // .data.calibration_report.calibrated_brier_score // .data.calibration_report.brier_score // 999) <= 0.26)' "$model_json"
elif [[ "$gate" == "75_to_100" ]]; then
  check_bool_with_json "5d evidence rows >= 60" "$(five_day_evidence_expr 60)" "$combined_json"
  check_bool_with_json "evaluated_rows_available >= 100" '(.data.evaluated_rows_available // (.data.summary_1d.evaluated_rows // 0)) >= 100' "$outcomes_json"
  check_bool_with_json "summary_1d.accuracy >= 0.50" '(.data.summary_1d.accuracy // 0) >= 0.50' "$outcomes_json"
  check_bool_with_json "calibration_report.effective_brier_score <= 0.24" '((.data.calibration_report.effective_brier_score // .data.calibration_report.calibrated_brier_score // .data.calibration_report.brier_score // 999) <= 0.24)' "$model_json"
fi

if [[ "$gate" == portfolio_* ]]; then
  check_bool_with_json "rollout_dry_run == false" '.data.rollout_dry_run == false' "$model_json"
  check_bool_with_json "portfolio_rollout_percentage is present" '(.data.portfolio_rollout_percentage // null) != null' "$model_json"
  check_bool_with_json "calibration_report.rows >= 30" '(.data.calibration_report.rows // 0) >= 30' "$model_json"
  check_bool_with_json "calibration_report.effective_brier_score <= 0.26" '((.data.calibration_report.effective_brier_score // .data.calibration_report.calibrated_brier_score // .data.calibration_report.brier_score // 999) <= 0.26)' "$model_json"

  if [[ "$gate" == "portfolio_20_to_35" ]]; then
    check_bool_with_json "portfolio_rollout_percentage == 20" '(.data.portfolio_rollout_percentage // -1) == 20' "$model_json"
    check_bool_with_json "5d evidence rows >= 20" "$(five_day_evidence_expr 20)" "$combined_json"
  elif [[ "$gate" == "portfolio_35_to_50" ]]; then
    check_bool_with_json "portfolio_rollout_percentage == 35" '(.data.portfolio_rollout_percentage // -1) == 35' "$model_json"
    check_bool_with_json "5d evidence rows >= 30" "$(five_day_evidence_expr 30)" "$combined_json"
  elif [[ "$gate" == "portfolio_50_to_75" ]]; then
    check_bool_with_json "portfolio_rollout_percentage == 50" '(.data.portfolio_rollout_percentage // -1) == 50' "$model_json"
    check_bool_with_json "5d evidence rows >= 40" "$(five_day_evidence_expr 40)" "$combined_json"
  else
    check_bool_with_json "portfolio_rollout_percentage == 75" '(.data.portfolio_rollout_percentage // -1) == 75' "$model_json"
    check_bool_with_json "5d evidence rows >= 60" "$(five_day_evidence_expr 60)" "$combined_json"
  fi
fi

echo
echo "Summary: PASS=$pass_count FAIL=$fail_count"
echo
echo "Observed metrics:"
echo "$outcomes_json" | jq '.data | {summary_1d, summary_5d, used_unevaluated_fallback, lookup_errors}'
echo "$model_json" | jq '.data | {model_loaded, model_load_error, rollout_percentage, portfolio_rollout_percentage, decision_logging, calibration_report: (.calibration_report // {rows:0,brier_score:null})}'

if [[ $fail_count -gt 0 ]]; then
  exit 1
fi
