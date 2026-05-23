# Moneybot Model Naming Tree

This document defines official naming for model families so logs, dashboards, artifacts, and rollout conversations stay unambiguous.

## Current production model (official)

- **Display name:** `Alpha Atlas`
- **Model version id:** `alpha-atlas-v1`
- **Fallback version id:** `alpha-atlas-v1-fallback`
- **Family:** `alpha-atlas`
- **Track:** deterministic logistic baseline

## Naming format (standard)

Use this pattern for all future models:

- **Display name:** `<Family Name> <Track/Type> <Major>`
- **Technical id:** `<family>-<track>-v<major>`

Examples:

- Display: `Alpha Atlas Logistic 1` → ID: `alpha-atlas-logistic-v1`
- Display: `Alpha Atlas XGBoost 2` → ID: `alpha-atlas-xgboost-v2`

## Canonical model tree

### Alpha Atlas Family (deterministic + supervised)

- `alpha-atlas-v1` (current champion baseline logistic)
- `alpha-atlas-v1-fallback` (built-in fallback artifact)
- `alpha-atlas-logistic-v2` (expanded feature set logistic)
- `alpha-atlas-xgboost-v1` (tree-based challenger)
- `alpha-atlas-lightgbm-v1` (gradient boosting challenger)
- `alpha-atlas-temporal-v1` (sequence/time-aware challenger)

### Alpha Atlas Ensemble Family

- `alpha-atlas-ensemble-v1` (weighted average ensemble)
- `alpha-atlas-ensemble-v2` (regime-aware ensemble)
- `alpha-atlas-stack-v1` (stacked metalearner ensemble)

## Rollout suffixes (optional metadata fields)

Keep model ids stable; track rollout phase in metadata/log fields, not by changing core ID:

- `phase=shadow`
- `phase=canary_10`
- `phase=canary_50`
- `phase=champion`

## Artifact naming suggestions

For future artifacts, prefer consistency:

- `data/models/alpha-atlas-v1.json`
- `data/models/alpha-atlas-v1.meta.json`
- `data/models/alpha-atlas-v1.history.json`
- `data/models/alpha-atlas-ensemble-v1.json`
