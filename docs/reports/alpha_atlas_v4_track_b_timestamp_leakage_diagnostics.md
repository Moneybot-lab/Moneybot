# Alpha Atlas V4 Track B timestamp-leakage diagnostics

Contract: `alpha-atlas-v4-prediction-execution-contract.v1`

Implemented repair evidence. All previously strict-xfailed scenarios now pass normally;
an xfail or xpass in this module is a regression. This report certifies only the
focused row-construction seams, not production routing or a trained V4 model.

| Scenario/test identifier | Status | Affected legacy function | Formerly leaked/unproven information | Implemented repair | Affected logic | V4 invariant/rule |
|---|---|---|---|---|---|---|
| `premarket_uses_only_previous_completed_daily_session` | pass | `_event_day`, `_row_before_or_on_indexed`, `build_training_rows_from_raw_market` | Same-session completed daily bar | `ExchangeCalendar` session cutoff before daily join | symbol | `feature_cutoff_at <= decision_at` |
| `premarket_excludes_current_session_final_symbol_ohlcv` | pass | `build_training_rows_from_raw_market` | Final close/high/low/full-day volume and derivatives | Filter bars by proven completion/availability | symbol | daily OHLCV completion rule |
| `premarket_market_context_uses_same_previous_session_cutoff` | pass | SPY/sector joins, `_market_regime_risk_on`, `_return_volatility` | Same-session SPY, sector, volatility and regime inputs | Apply one cutoff independently to every family | SPY, sector, regime | common feature cutoff |
| `premarket_label_is_entry_open_to_fifth_session_close` | pass | `build_training_rows_from_raw_market`, `split_adjusted_forward_return` | Close-to-close `idx + 5` label | Materialize entry open, `label_start_at`, and S0-S4 exit close | entry, label | `decision_at < entry_at == label_start_at < exit_at` |
| `regular_session_excludes_current_daily_bar_and_derivatives` | pass | `_row_before_or_on_indexed`, feature calculators | Final daily OHLCV, RSI/MACD/SMA/return/volatility | Prior completed daily session or timestamp-safe intraday inputs | symbol | feature cutoff and completed-bar rule |
| `regular_session_context_obeys_symbol_feature_cutoff` | pass | SPY/sector joins, regime calculation | Same-session context aggregates | Apply symbol cutoff to every context family | SPY, sector, regime | common feature cutoff |
| `regular_session_label_starts_at_next_eligible_open` | pass | forward-label block in `build_training_rows_from_raw_market` | Same-session close treated as label start | Use next eligible open and S0-S4 close | entry, label | execution ordering |
| `after_hours_requires_proven_current_bar_availability` | pass | `_normalize_market_row`, `load_market_history`, builder join | Calendar date treated as availability proof | Persist provider availability/bar-close and fail closed when absent | symbol, SPY, sector, regime | proven availability at cutoff |
| `after_hours_label_starts_at_next_eligible_open` | pass | forward-label block in `build_training_rows_from_raw_market` | Current close used instead of next open | Materialize next-session entry and S0-S4 exit | entry, label | execution ordering |
| `outcome_market_date_uses_new_york_date_when_utc_date_differs` | pass | `event_market_date` | UTC date substituted for exchange date | Resolve America/New_York official session | entry, label | official calendar semantics |
| `builder_event_day_uses_new_york_date_across_dst_boundary` | pass | `_event_day` | UTC date across EDT boundary | Use official exchange-calendar resolver | symbol, context, label | official calendar semantics |
| `official_early_close_ends_regular_session_at_1300_eastern` | pass | `ExchangeCalendar.session_at` | Hardcoded 16:00 close | Add official early-close schedule | entry, daily availability | official session close |
| `stale_symbol_bar_fails_closed` | pass | builder daily join | Unmeasured stale symbol bar | Require timestamps/tolerance and reject stale input | symbol | fail closed |
| `missing_required_spy_and_regime_context_fails_closed` | pass | SPY join, `build_alpha_atlas_v3_features`, regime calculation | Missing context becomes nullable/fillable | Reject absent required family before feature filling | SPY, regime | fail closed/common cutoff |
| `stale_spy_and_regime_context_fails_closed` | pass | SPY join, regime calculation | Stale context accepted without availability timestamps | Enforce per-family timestamp/tolerance | SPY, regime | fail closed/common cutoff |
| `missing_required_entry_open_fails_closed` | pass | forward-label block | Missing executable open is ignored by close-to-close label | Require documented entry price | entry, label | `entry_at == label_start_at` |
| `premarket_split_requires_point_in_time_action_availability` | pass | `adjust_bars_to_asof`, builder split application | Decision-date action lacks publication timestamp | Add point-in-time action availability and identity provenance | symbol, label | action known by cutoff |
| `unix_normalization_preserves_the_decision_instant` | pass | `normalize_unix_ts` | Millisecond epoch is treated as seconds | Normalize supported epoch magnitudes before session resolution | decision | UTC timestamp storage |
| `market_date_helpers_are_deterministic_but_not_availability_proof` | pass | `_market_date`, `_market_date_index`, `_row_before_or_on_indexed` | Seam documented; date is not availability | Replace join policy, not deterministic parsing | symbol | availability requires timestamp |
| `weekend_and_holiday_do_not_create_fictional_market_bars` | pass | `_row_before_or_on_indexed` | No fabricated daily bar when data has no closed-day row | Add entry/calendar timestamps later | symbol | completed eligible sessions |
| `contract_rejects_feature_source_after_cutoff` | pass | V4 typed contract | Post-cutoff source rejected | Preserve validation | all feature families | `feature_cutoff_at <= decision_at` |
| `missing_forward_price_rejects_row` | pass | builder forward-window guard | No guessed label price | Preserve fail-closed guard while changing execution rule | label | valid exit required |
| `split_in_forward_horizon_is_adjusted_and_provenanced` | pass | `split_adjusted_forward_return`, builder provenance | Split adjustment ID/factor retained | Preserve behavior in executable-price labels | label | corporate-action provenance |
| `new_listing_with_insufficient_history_fails_closed` | pass | builder history guard | Insufficient history rejected | Preserve rejection without padding | symbol | fail closed |
| `ticker_change_without_point_in_time_identity_mapping_fails_closed` | pass | builder symbol-history guard | Unknown new identity rejected | Add point-in-time mapping without guessing | symbol | fail closed/identity provenance |
| `delisting_or_missing_terminal_price_fails_closed` | pass | builder forward-window guard | No invented terminal price | Preserve rejection pending versioned terminal policy | label | valid exit required |
| `market_calendar_safe_basics_cover_premarket_holiday_weekend_and_dst` | pass | `ExchangeCalendar` | Basic holiday/weekend/DST conversion is safe | Replace/extend with official early-close calendar | session | official calendar semantics |
| `load_market_history_keeps_daily_date_but_does_not_claim_availability` | pass | `load_market_history` | Explicitly demonstrates availability metadata is absent | Add provider bar-close/availability provenance | symbol | proven availability at cutoff |

Timing, executable-price and per-family provenance are now asserted directly. Breadth
remains deferred because no breadth family exists in the current Track B row schema.

## Implemented policy and compatibility

- Dataset rows and the cleaner now use `massive-decision-training-rows.v4`. The
  homogeneous cleaned-row reader accepts either V2 or V4 but rejects mixed schemas;
  production comparison/promotion evidence remains pinned to V2.
- Shared calendar `XNYS-rule-calendar.v1` applies recurring U.S. equity holidays,
  official early-close rules, New York daylight-saving conversion, and UTC open/close.
  It adds no deploy dependency, so GitHub Actions and Render installation are unchanged.
- Date-only flat-file bars are conservatively available at official close only for a
  prior completed session. A same-session after-hours aggregate requires explicit
  `available_at <= feature_cutoff_at`; otherwise the attempted row is rejected.
- Symbol, SPY, sector, volatility and regime families independently enforce a default
  three-session staleness tolerance. Required context rejects rather than imputes.
- Labels enter at the first eligible official open strictly after the decision (same
  day for premarket), set `entry_at == label_start_at`, and exit at S4/S9 official
  close for five/ten-session horizons.
- Decision-time features use only corporate actions explicitly available by cutoff,
  or actions effective before the decision session. Realized labels may use actions
  inside the forward horizon and retain IDs and cumulative factors.
