"""Explicit runtime eligibility; this is not model promotion certification."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .deterministic_model import FEATURE_COLUMNS, validate_artifact_dimensions
from .alpha_atlas_feature_contract import ALPHA_ATLAS_V2_FEATURES
from .alpha_atlas_v3_features import ALPHA_ATLAS_V3_FEATURES

DAY1_VERSIONS = frozenset({'day1-logreg-v1', 'alpha-atlas-v1', 'alpha-atlas-v1-fallback'})
# The original five-input artifact and the ten-input news extension both have
# an existing Day-1 builder. Neither matches the ten feature_* V3.1 inputs.
DAY1_SCHEMAS = (frozenset(FEATURE_COLUMNS[:5]), frozenset(FEATURE_COLUMNS))


@dataclass(frozen=True)
class ServingFeatureContract:
    classification: str
    builder: str | None
    expected_count: int
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.classification in {'SUPPORTED_DAY1', 'SUPPORTED_V3'}


def resolve_serving_feature_contract(artifact) -> ServingFeatureContract:
    """Keep serialized ordering; reject ambiguous or nonfinite learned state."""
    try:
        names = artifact.feature_columns
        if not isinstance(names, (list, tuple)) or any(not isinstance(n, str) or not n for n in names):
            raise ValueError('invalid feature names')
        validate_artifact_dimensions(artifact)
        for values in (artifact.means, artifact.stds, artifact.weights):
            if any(not math.isfinite(float(v)) for v in values):
                raise ValueError('nonfinite state')
        if any(float(v) <= 0 for v in artifact.stds):
            raise ValueError('invalid scale')
        if not all(math.isfinite(float(v)) for v in (artifact.bias, artifact.calibration_slope, artifact.calibration_intercept, artifact.decision_threshold)):
            raise ValueError('nonfinite scalar')
        if not 0 <= artifact.decision_threshold <= 1:
            raise ValueError('invalid threshold')
        lo, hi = artifact.clip_lower, artifact.clip_upper
        if (lo is None) != (hi is None):
            raise ValueError('incomplete clipping')
        if lo is not None and any(not math.isfinite(float(a)) or not math.isfinite(float(b)) or a > b for a, b in zip(lo, hi)):
            raise ValueError('invalid clipping')
        if not isinstance(artifact.version, str) or not artifact.version:
            raise ValueError('invalid version')
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ServingFeatureContract('INVALID_ARTIFACT', None, 0, 'invalid_model_artifact')
    schema = frozenset(names)
    n = len(names)
    if schema == frozenset(ALPHA_ATLAS_V2_FEATURES):
        return ServingFeatureContract('BLOCKED_V2_LEAKAGE', None, n, 'v2_leakage_contract')
    version = artifact.version
    if schema in DAY1_SCHEMAS and version in DAY1_VERSIONS:
        return ServingFeatureContract('SUPPORTED_DAY1', 'legacy_day1_contract', n)
    if schema == frozenset(ALPHA_ATLAS_V3_FEATURES) and version in {'candidate-alpha-atlas-v3-clean-v1', 'alpha-atlas-v3'}:
        return ServingFeatureContract('SUPPORTED_V3', 'alpha-atlas-v3-market-state.v1', n)
    return ServingFeatureContract('UNSUPPORTED_SCHEMA', None, n, 'unsupported_feature_contract')
