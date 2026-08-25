# Alpha Atlas V4 implementation map

**Planning evidence only — none of these paths has been migrated in this change.**

## Existing timing owners and later repair targets

| Concern | Existing files/functions | Current semantic to revisit |
|---|---|---|
| Decision time and export | `moneybot/services/decision_log.py`: `DecisionLogger.log`, `read_decision_events`; `moneybot/api.py`: `export_decision_log`; `moneybot/services/outcome_tracking.py`: `normalize_unix_ts`, `event_market_date` | Logger writes integer wall-clock `ts`; export passes records through; downstream conversion loses session context. Add decision ID and UTC timestamp/provenance without changing legacy exports prematurely. |
| Massive daily selection | `scripts/build_massive_decision_training_rows.py`: `_market_date`, `_event_day`, `_market_date_index`, `_row_before_or_on_indexed`, `load_market_history`, `build_training_rows_from_raw_market` | Timestamp becomes UTC date; bisect includes a same-date completed daily bar. This is the primary leakage-repair target. |
| Intraday/provider bars | `moneybot/services/market_data_providers.py`: `MassiveRestClient.normalize_timestamp`, `minute_aggregates`, `MarketCalendar.session_at`; `moneybot/services/market_stream.py`: `parse_stream_event`; `moneybot/services/market_data.py`: `_intraday_breakout_snapshot`, `get_price_history_data` | Timestamped minute/stream data exists, but V3 dataset construction does not establish bar-close availability under a shared cutoff. |
| V3/V3.1 features | `moneybot/services/alpha_atlas_v3_features.py`: `_bar_date`, `normalize_daily_bars`, `build_alpha_atlas_v3_features`; `moneybot/services/alpha_atlas_v31_quantitative.py`; `moneybot/services/alpha_atlas_feature_contract.py` | V3 normalizes timestamps to dates and accepts daily bars through `asof_date`; V3.1 reuses the V3 feature engine. Keep frozen. |
| SPY, sector, volatility, regime | `scripts/build_massive_decision_training_rows.py`: `_sector_benchmark_symbol`, `_market_regime_risk_on`, `_return_volatility`, `_beta_to_benchmark`, and the SPY/sector joins inside `build_training_rows_from_raw_market`; `moneybot/services/alpha_atlas_v3_features.py`: `build_alpha_atlas_v3_features` | SPY and sector use the same date-inclusive join; volatility proxy and risk regime are SPY-derived. Future breadth/volatility sources need independent provenance timestamps. |
| Forward labels/outcomes | `scripts/build_massive_decision_training_rows.py`: `build_training_rows_from_raw_market`, `split_adjusted_forward_return`; `moneybot/services/decision_target.py`: `label_from_forward_return`; `scripts/day8_build_decision_training_dataset.py`: `_future_return`, `build_rows`; `moneybot/services/outcome_tracking.py`: `dated_close_values`, `evaluate_decision_events`; `moneybot/api.py`: `_price_path_for_outcomes`, `_future_return_for_outcomes` | Canonical Track B labels selected close to `idx + N` close; legacy/yfinance outcomes index daily closes from event date. Migrate to entry-open/session-count/exit-close. |
| Backtest entry/exit economics | `scripts/backtest_challenger_suite.py`: `_canonical_economic_frame`, `_cohort_economics`, `backtest_challenger_suite`; V3 report generation in `scripts/train_alpha_atlas_v3_candidate.py`; V3.1 evaluation in `scripts/train_alpha_atlas_v31_candidate.py` | Backtest consumes endpoint forward returns, applies round-trip bps, and does not reconstruct executable timestamps or a mark-to-market path. |
| Serving and dry runs | `scripts/train_alpha_atlas_v3_candidate.py`: `build_serving_dry_runs`, `attach_v3_contract`; `scripts/train_alpha_atlas_v31_candidate.py`: `_serving_dry_runs`; `moneybot/services/production_servability.py`: `certify_candidate`, `validate_certification`; `moneybot/services/deterministic_advisor.py`; `moneybot/services/ai_advisor.py`; decision endpoints `quick_ask` and `explain_recommendation` in `moneybot/api.py` | Representative runs fetch undifferentiated daily history; certification checks declared `T` semantics rather than V4 timestamp provenance. Production stays unchanged until a separately approved shadow migration. |

## Recommended migration order

1. Add an official exchange-calendar adapter and tested session/cutoff resolver.
2. Extend decision/shadow logging with immutable decision IDs and V4 provenance,
   without routing V4 predictions.
3. Add cutoff-aware daily and intraday readers that return source availability and
   rejection reasons; then apply corporate-action identity handling.
4. Build a new V4-only feature engine and migrate symbol, SPY, sector, volatility,
   breadth, and regime families together.
5. Replace V4 label construction with official entry-open and S4/S9 exit-close
   execution records; add halted/delisted exception policies.
6. Add adversarial leakage tests and immutable dataset manifests before producing a
   new V4 research dataset.
7. Update V4 evaluation/backtests and representative **shadow** serving to consume
   the same record. Validate costs and slippage.
8. Only after separate review and promotion evidence, consider certification and
   production routing changes. V3.1 remains benchmark-only throughout this work.
