# Alpha Atlas V4 implementation map

**Prompt 3 status:** Track B row-construction timing migration is implemented and
research-only. V3/V3.1, production serving, promotion and routing remain unchanged.

## Implemented components

| Concern | Files/functions | Prompt 3 status |
|---|---|---|
| Timestamp normalization | `moneybot/services/outcome_tracking.py`: `normalize_unix_ts`, `event_market_date` | Seconds, milliseconds, microseconds and nanoseconds normalize to UTC; invalid values fail closed; exchange dates use New York. |
| Exchange sessions | `moneybot/services/market_data_providers.py`: `ExchangeCalendar` | Shared `XNYS-rule-calendar.v1` supplies holidays, DST, recurring early closes, previous/next sessions and UTC official opens/closes without a new deploy dependency. |
| Daily availability | `scripts/build_massive_decision_training_rows.py`: `_normalize_market_row`, `_feature_index_for_decision`, `load_market_history` | Prior completed bars use official-close availability; same-session after-hours bars require explicit provider availability. |
| Symbol/context features | `build_training_rows_from_raw_market`, `_market_regime_risk_on`, `_return_volatility`, SPY/sector joins | Symbol, SPY, sector, volatility and regime families share the cutoff, resolve independently and reject missing/stale required sources. |
| Executable labels | `build_training_rows_from_raw_market`, `split_adjusted_forward_return` | Entry is next eligible open; label start equals entry; five/ten-session exits are S4/S9 official closes. |
| Corporate actions | `moneybot/services/corporate_actions.py`, `_feature_safe_splits` | Feature actions must be known by cutoff (or effective before the decision session); future-horizon action IDs/factors remain available only to realized labels. |
| Provenance/schema | `AlphaAtlasV4TimingRecord`, row emission and `write_rows` | Rows emit timing, per-family source/availability, prices, calendar, identity, staleness, actions, commit and manifest identity under `massive-decision-training-rows.v4`. |

## Compatibility and intentionally deferred work

- V1/V2 Track B rows are not reinterpreted. The cleaner requires V4; the homogeneous
  training reader accepts V2 or V4 but rejects mixed schemas; production comparison
  and promotion evidence remain pinned to V2.
- No V4 dataset was generated, ingested, deduplicated, trained, promoted or routed.
- Timestamp-safe intraday V4 features and breadth inputs remain deferred because the
  current Track B daily row schema has neither family.
- A maintained external exchange-calendar package can replace the versioned rule
  adapter later; that change requires parity tests and a new calendar identifier.
