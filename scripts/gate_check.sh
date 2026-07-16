#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 10_to_25
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 25_to_50
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 50_to_75
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate 75_to_100
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_10_to_25
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_25_to_50
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_50_to_75
  BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_75_to_100

Options:
  --gate <10_to_25|25_to_50|50_to_75|75_to_100|portfolio_10_to_25|portfolio_25_to_50|portfolio_50_to_75|portfolio_75_to_100>
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

case "$gate" in
  10_to_25|25_to_50|50_to_75|75_to_100|portfolio_10_to_25|portfolio_25_to_50|portfolio_50_to_75|portfolio_75_to_100|portfolio_20_to_35|portfolio_35_to_50)
    ;;
  *)
    echo "--gate must be one of: 10_to_25, 25_to_50, 50_to_75, 75_to_100, portfolio_10_to_25, portfolio_25_to_50, portfolio_50_to_75, portfolio_75_to_100" >&2
    usage
    exit 2
    ;;
esac

require_cmd curl
require_cmd jq

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
model_file="$tmp_dir/model-health.json"
outcomes_file="$tmp_dir/decision-outcomes.json"
combined_file="$tmp_dir/combined.json"

curl -fsS "${BASE_URL}/api/model-health" > "$model_file"
curl -fsS "${BASE_URL}/api/decision-outcomes?limit=${limit}&force_live=true" > "$outcomes_file"

if ! jq -e '.data' "$model_file" >/dev/null; then
  echo "model-health response missing .data" >&2
  exit 2
fi
if ! jq -e '.data' "$outcomes_file" >/dev/null; then
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

check_bool_with_file() {
  local label="$1"
  local expr="$2"
  local file="$3"
  if jq -e "$expr" "$file" >/dev/null 2>&1; then
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
check_bool_with_file "model_loaded == true" '.data.model_loaded == true' "$model_file"
check_bool_with_file "model_load_error is empty" '((.data.model_load_error // "") | tostring | length) == 0' "$model_file"
check_bool_with_file "decision_logging.enabled == true" '.data.decision_logging.enabled == true' "$model_file"
check_bool_with_file "used_unevaluated_fallback == false" '.data.used_unevaluated_fallback == false' "$outcomes_file"
check_bool_with_file "lookup_errors == 0" '(.data.lookup_errors // 0) == 0' "$outcomes_file"

jq -n --slurpfile outcomes "$outcomes_file" --slurpfile model "$model_file" '{outcomes: $outcomes[0].data, model: $model[0].data}' > "$combined_file"
five_day_evidence_expr() {
  local min_rows="$1"
  printf '((.outcomes.summary_5d.evaluated_rows // .outcomes.evaluated_rows_5d_available // 0) >= %s) or ((.model.calibration_report.rows // 0) >= %s)' "$min_rows" "$min_rows"
}

run_quick_gate() {
  local current_pct="$1"
  local min_5d_rows="$2"
  local min_evaluated_rows="$3"
  local min_accuracy="$4"
  local min_calibration_rows="$5"
  local max_brier="$6"

  check_bool_with_file "rollout_percentage == ${current_pct}" "(.data.rollout_percentage // -1) == ${current_pct}" "$model_file"
  check_bool_with_file "5d evidence rows >= ${min_5d_rows}" "$(five_day_evidence_expr "$min_5d_rows")" "$combined_file"
  check_bool_with_file "evaluated_rows_available >= ${min_evaluated_rows}" "(.data.evaluated_rows_available // (.data.summary_1d.evaluated_rows // 0)) >= ${min_evaluated_rows}" "$outcomes_file"
  check_bool_with_file "summary_1d.accuracy >= ${min_accuracy}" "(.data.summary_1d.accuracy // 0) >= ${min_accuracy}" "$outcomes_file"
  check_bool_with_file "calibration_report.rows >= ${min_calibration_rows}" "(.data.calibration_report.rows // 0) >= ${min_calibration_rows}" "$model_file"
  check_bool_with_file "calibration_report.effective_brier_score <= ${max_brier}" "((.data.calibration_report.effective_brier_score // .data.calibration_report.calibrated_brier_score // .data.calibration_report.brier_score // 999) <= ${max_brier})" "$model_file"
}

case "$gate" in
  10_to_25)
    run_quick_gate 10 10 20 0.46 20 0.28
    ;;
  25_to_50)
    run_quick_gate 25 20 40 0.48 30 0.26
    ;;
  50_to_75)
    run_quick_gate 50 40 75 0.49 45 0.25
    ;;
  75_to_100)
    run_quick_gate 75 60 100 0.50 60 0.24
    ;;
esac

if [[ "$gate" == portfolio_* ]]; then
  check_bool_with_file "rollout_dry_run == false" '.data.rollout_dry_run == false' "$model_file"
  check_bool_with_file "portfolio_rollout_percentage is present" '(.data.portfolio_rollout_percentage // null) != null' "$model_file"
  check_bool_with_file "calibration_report.rows >= 30" '(.data.calibration_report.rows // 0) >= 30' "$model_file"
  check_bool_with_file "calibration_report.effective_brier_score <= 0.26" '((.data.calibration_report.effective_brier_score // .data.calibration_report.calibrated_brier_score // .data.calibration_report.brier_score // 999) <= 0.26)' "$model_file"

  case "$gate" in
    portfolio_10_to_25)
      check_bool_with_file "portfolio_rollout_percentage == 10" '(.data.portfolio_rollout_percentage // -1) == 10' "$model_file"
      check_bool_with_file "5d evidence rows >= 10" "$(five_day_evidence_expr 10)" "$combined_file"
      ;;
    portfolio_25_to_50)
      check_bool_with_file "portfolio_rollout_percentage == 25" '(.data.portfolio_rollout_percentage // -1) == 25' "$model_file"
      check_bool_with_file "5d evidence rows >= 20" "$(five_day_evidence_expr 20)" "$combined_file"
      ;;
    portfolio_20_to_35)
      check_bool_with_file "portfolio_rollout_percentage == 20" '(.data.portfolio_rollout_percentage // -1) == 20' "$model_file"
      check_bool_with_file "5d evidence rows >= 20" "$(five_day_evidence_expr 20)" "$combined_file"
      ;;
    portfolio_35_to_50)
      check_bool_with_file "portfolio_rollout_percentage == 35" '(.data.portfolio_rollout_percentage // -1) == 35' "$model_file"
      check_bool_with_file "5d evidence rows >= 30" "$(five_day_evidence_expr 30)" "$combined_file"
      ;;
    portfolio_50_to_75)
      check_bool_with_file "portfolio_rollout_percentage == 50" '(.data.portfolio_rollout_percentage // -1) == 50' "$model_file"
      check_bool_with_file "5d evidence rows >= 40" "$(five_day_evidence_expr 40)" "$combined_file"
      ;;
    portfolio_75_to_100)
      check_bool_with_file "portfolio_rollout_percentage == 75" '(.data.portfolio_rollout_percentage // -1) == 75' "$model_file"
      check_bool_with_file "5d evidence rows >= 60" "$(five_day_evidence_expr 60)" "$combined_file"
      ;;
  esac
fi

echo
echo "Summary: PASS=$pass_count FAIL=$fail_count"
echo
echo "Observed metrics:"
jq '.data | {summary_1d, summary_5d, used_unevaluated_fallback, lookup_errors}' "$outcomes_file"
jq '.data | {model_loaded, model_load_error, rollout_percentage, portfolio_rollout_percentage, decision_logging, calibration_report: (.calibration_report // {rows:0,brier_score:null})}' "$model_file"

if [[ $fail_count -gt 0 ]]; then
  exit 1
fi
