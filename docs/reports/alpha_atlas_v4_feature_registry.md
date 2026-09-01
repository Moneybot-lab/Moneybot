# Alpha Atlas V4 feature registry

**Schema:** `alpha-atlas-v4-feature-registry.v2`
**Feature contract:** `alpha-atlas-v4-features.v2`

## Exact reconciliation

**48 feature-store columns = 43 model inputs + 5 provenance columns.** The five non-model columns are: `feature_cutoff_at`, `feature_family_available_at`, `feature_family_source_at`, `feature_market_asof_date`, `feature_split_ids`.

The old count of 43 is the intended numeric model vector. The hosted count of 48 used the feature-store prefix convention and included five provenance columns. No unintended model feature was found.

## Registry

| Column | Classification | Source family | Calculation | Lookback | Missing/fill policy | Reconstructability |
|---|---|---|---|---:|---|---|
| `feature_above_vwap` | model_input | symbol | 1 iff close(T)>VWAP(T) | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_atr_14` | model_input | symbol | mean true range over T-13..T | 14 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_close` | model_input | symbol | split-basis close(T) | 1 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_cutoff_at` | provenance | lineage | lineage field | — | required_for_reconstruction | lineage only |
| `feature_distance_from_20d_low` | model_input | symbol | close(T)/min(low T-19..T)-1 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_dollar_volume` | model_input | symbol | close(T)*volume(T) | 1 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_drawdown_from_20d_high` | model_input | symbol | close(T)/max(high T-19..T)-1 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_ema_10` | model_input | symbol | EMA10(close through T) | 10 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_ema_20` | model_input | symbol | EMA20(close through T) | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_family_available_at` | provenance | lineage | lineage field | — | required_for_reconstruction | lineage only |
| `feature_family_source_at` | provenance | lineage | lineage field | — | required_for_reconstruction | lineage only |
| `feature_gap_percent` | model_input | symbol | open(T)/close(T-1)-1 | 2 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_macd` | model_input | symbol | EMA12(close)-EMA26(close) | 35 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_macd_hist` | model_input | symbol | MACD-MACD signal | 35 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_macd_signal` | model_input | symbol | EMA9(MACD) | 35 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_market_asof_date` | provenance | lineage | lineage field | — | required_for_reconstruction | lineage only |
| `feature_market_regime_risk_on` | model_input | spy_context | 1 iff SPY return5>0 and SPY close>=SMA20 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_market_volatility_proxy` | model_input | spy_context | population stddev of SPY backward returns | 21 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_momentum_5d_vs_20d` | model_input | symbol | return5-return20 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_price_vs_sma_20` | model_input | symbol | close(T)/SMA20-1 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_price_vs_sma_50` | model_input | symbol | close(T)/SMA50-1 | 50 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_price_vs_vwap` | model_input | symbol | close(T)/VWAP(T)-1 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_relative_volume_5d` | model_input | symbol | volume(T)/mean(volume T-4..T) | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_return_10d_lagged` | model_input | symbol | close(T)/close(T-10)-1 | 10 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_return_1d_lagged` | model_input | symbol | close(T)/close(T-1)-1 | 1 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_return_20d_lagged` | model_input | symbol | close(T)/close(T-20)-1 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_return_5d_lagged` | model_input | symbol | close(T)/close(T-5)-1 | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_rsi_14` | model_input | symbol | simple-average RSI14 through T | 15 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sector_relative_return_5d` | model_input | sector_context | symbol return5-sector return5 | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sma_10` | model_input | symbol | mean(close T-9..T) | 10 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sma_10_over_20` | model_input | symbol | SMA10/SMA20 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sma_20` | model_input | symbol | mean(close T-19..T) | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sma_20_over_50` | model_input | symbol | SMA20/SMA50 | 50 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_sma_50` | model_input | symbol | mean(close T-49..T) | 50 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_split_ids` | provenance | lineage | lineage field | — | required_for_reconstruction | lineage only |
| `feature_spy_return_1d` | model_input | spy_context | SPY close(T)/SPY close(T-1)-1 | 1 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_spy_return_5d` | model_input | spy_context | SPY close(T)/SPY close(T-5)-1 | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_symbol_beta_20d` | model_input | spy_context | cov(symbol,SPY)/var(SPY) over 20 returns | 21 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_symbol_minus_spy_5d` | model_input | spy_context | symbol return5-SPY return5 | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_trend_slope_10d` | model_input | symbol | OLS slope of close over 10 sessions | 10 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_trend_slope_20d` | model_input | symbol | OLS slope of close over 20 sessions | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_volatility_20d` | model_input | symbol | population stddev of 20 backward symbol returns | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_volatility_5d` | model_input | symbol | population stddev of 5 backward symbol returns | 5 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_volume` | model_input | symbol | split-basis volume(T) | 1 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_volume_ratio_20d` | model_input | symbol | volume(T)/mean(volume T-19..T) | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_volume_zscore_20d` | model_input | symbol | (volume(T)-mean20)/population_stddev20 | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_vwap` | model_input | symbol | sum(close*volume T-19..T)/sum(volume T-19..T) | 20 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |
| `feature_vwap_slope` | model_input | symbol | OLS slope of VWAP over configured trailing window | 29 | fit_period_median_after_required-family fail_closed checks | REQUIRES_IMMUTABLE_SOURCE_LINEAGE |

Prior-request state, including `feature_probability_up_delta_from_last_signal`, is not registered as a model input and remains request-audit metadata only.
