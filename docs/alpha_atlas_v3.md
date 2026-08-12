# Alpha Atlas V3 clean successor workflow

Alpha Atlas V3 is a **candidate contract**, not a production version. The
training artifact keeps the source version
`candidate-alpha-atlas-v3-clean-v1`; `day14_promote_candidate.py` assigns
`alpha-atlas-v3` only after a later human-approved promotion.

## Contract

All inputs are computed by
`moneybot.services.alpha_atlas_v3_features.build_alpha_atlas_v3_features`
from adjusted, ascending daily bars at or before prediction date `T`.

| Feature | Definition |
|---|---|
| `feature_return_1d_lagged` | `close(T) / close(T-1 trading bar) - 1` |
| `feature_return_5d_lagged` | `close(T) / close(T-5 trading bars) - 1` |
| `feature_return_20d_lagged` | `close(T) / close(T-20 trading bars) - 1` |
| `feature_rsi_14` | RSI from simple average gains/losses over the last 14 trading-bar changes |
| `feature_macd_hist` | `(EMA12(close)-EMA26(close))-EMA9(MACD)`, through `T` |
| `feature_volume_ratio_20d` | `volume(T) / mean(volume(T-19..T))` |
| `feature_price_vs_sma_20` | `close(T) / mean(close(T-19..T)) - 1` |
| `feature_volatility_20d` | Population standard deviation of the last 20 backward one-bar decimal returns |
| `feature_spy_return_5d` | SPY backward five-trading-bar decimal return |
| `feature_symbol_minus_spy_5d` | Symbol backward 5-bar return minus SPY backward 5-bar return |
| `feature_market_regime_risk_on` | `1` when SPY's backward 5-bar return is positive and SPY is at/above its SMA20 at `T`; otherwise `0` |

The label is the canonical Massive Track B `label_up_5d`; `return_5d` is
measured from the symbol close at `T` to the close five subsequent trading
bars later and is never included in the feature allowlist. Same-event
probabilities, recommendations, decision sources, prior application outputs,
forward returns, realized outcomes, and labels are forbidden.

`moneybot.services.decision_target` is authoritative for this target across
the Massive row builder, cleaning, challenger training, challenger backtests,
and V3 artifact metadata. Return buckets remain profit-utility and tail-risk
diagnostics rather than an alternative training label.

Fit-period medians are the only fill values learned. They are persisted in the
artifact and reused unchanged for calibration, threshold selection, final
test, and serving. The canonical Track B purge uses a five-day label horizon
and one-day embargo.

## Generate review artifacts

First build and clean the canonical Massive dataset using the existing Track B
workflow. Then run:

```bash
python scripts/train_alpha_atlas_v3_candidate.py \
  --train data/track_b/training_quality/cleaned_train.jsonl \
  --test data/track_b/training_quality/cleaned_test.jsonl \
  --all-cleaned data/track_b/training_quality/cleaned_all.jsonl \
  --output-dir data/track_b/alpha_atlas_v3
```

The command requires configured Massive history because certification dry-runs
use `MarketDataService.get_price_history_data()` for representative symbols.
In GitHub Actions, a missing `MASSIVE_API_KEY` skips only the V3 review
candidate and writes `v3_generation_status.json`; the base Track B challenger
run continues. No candidate or certification is synthesized in that case.
It writes:

* `candidate_alpha_atlas_v3_clean.json`
* `alpha_atlas_v3_model_report.json`
* `alpha_atlas_v3_feature_coverage_report.json`
* `alpha_atlas_v3_backtest_report.json`
* `alpha_atlas_v3_representative_serving_dry_runs.json`
* `production_servability_certification.json`
* `alpha_atlas_v3_recovery_rebaseline_report.json`

The recovery report always sets `automatic_promotion=false`. The workflow does
not call Day 14 or the protected promotion endpoint. Alpha Atlas V2 is marked
as a structurally failed, non-apples-to-apples baseline; its contaminated
legacy metrics do not become a silent override of the clean candidate's
results. Certification remains mandatory and fail-closed.
