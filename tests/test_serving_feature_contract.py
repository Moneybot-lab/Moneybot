import json
from unittest.mock import Mock

import numpy as np
import pytest

from moneybot.services.alpha_atlas_v3_features import ALPHA_ATLAS_V3_FEATURES
from moneybot.services.alpha_atlas_feature_contract import ALPHA_ATLAS_V2_FEATURES
from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.deterministic_model import BaselineModelArtifact, FEATURE_COLUMNS
from moneybot.services.serving_feature_contract import resolve_serving_feature_contract


def write_model(tmp_path, columns, version='candidate-alpha-atlas-v31-clean-v1', **changes):
    n = len(columns)
    a = BaselineModelArtifact(version, list(columns), [0.]*n, [1.]*n, [.1]*n, .1, .55)
    payload = a.to_dict()
    payload.update(changes)
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(payload))
    return path


@pytest.mark.parametrize('columns,version', [
    (ALPHA_ATLAS_V3_FEATURES[:-1], 'candidate-alpha-atlas-v31-clean-v1'),
    ([f'unknown{i}' for i in range(10)], 'alpha-atlas-v1'),
    ([f'unknown{i}' for i in range(11)], 'alpha-atlas-v3'),
    (ALPHA_ATLAS_V3_FEATURES[:4], 'alpha-atlas-v3'),
    ((*ALPHA_ATLAS_V3_FEATURES, 'extra'), 'alpha-atlas-v3'),
    ((*ALPHA_ATLAS_V3_FEATURES, ALPHA_ATLAS_V3_FEATURES[0]), 'alpha-atlas-v3'),
    ([], 'alpha-atlas-v1'),
    (FEATURE_COLUMNS, 'alpha-atlas-v2'),
    (FEATURE_COLUMNS, 'candidate-alpha-atlas-v31-clean-v1'),
    (ALPHA_ATLAS_V3_FEATURES, 'alpha-atlas-v1'),
    (ALPHA_ATLAS_V2_FEATURES, 'alpha-atlas-v1'),
])
def test_blocked_artifacts_never_score_or_create_forecasts(tmp_path, monkeypatch, columns, version):
    path = write_model(tmp_path, columns, version)
    a = DeterministicQuickAdvisor(enabled=True, artifact_path=str(path))
    scorer = Mock(side_effect=AssertionError('scorer must not execute'))
    envelope = Mock(side_effect=AssertionError('forecast must not be created'))
    history = Mock(side_effect=AssertionError('history must not be fetched'))
    a.set_market_history_service(history)
    monkeypatch.setattr('moneybot.services.deterministic_advisor.predict_proba', scorer)
    monkeypatch.setattr('moneybot.services.deterministic_advisor.make_forecast', envelope)
    args = dict(signal_data={}, quote_data={'price': 100}, symbol='TEST')
    assert a.predict_quick_decision(**args) is None
    assert a.predict_shadow_decision(**args) is None
    assert a.predict_portfolio_position(**args, entry_price=100, current_price=100, shares=1) is None
    assert not a.last_feature_contract_diagnostics['feature_contract_servable']
    scorer.assert_not_called()
    envelope.assert_not_called()
    history.get_price_history_data.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'means': [0.]}, {'weights': [0.]}, {'stds': [0.]*10},
    {'bias': float('nan')}, {'weights': [float('inf')]*10},
    {'clip_lower': [0.]*10}, {'clip_lower': [2.]*10, 'clip_upper': [1.]*10},
])
def test_invalid_learned_state_is_blocked(tmp_path, monkeypatch, changes):
    a = DeterministicQuickAdvisor(enabled=True, artifact_path=str(write_model(tmp_path, FEATURE_COLUMNS, 'alpha-atlas-v1', **changes)))
    scorer = Mock(side_effect=AssertionError('invalid state'))
    monkeypatch.setattr('moneybot.services.deterministic_advisor.predict_proba', scorer)
    assert a.predict_quick_decision(signal_data={'volume_ratio': 1}, quote_data={}) is None
    assert a.last_feature_contract_diagnostics['feature_contract_classification'] == 'INVALID_ARTIFACT'
    scorer.assert_not_called()


@pytest.mark.parametrize('columns', [FEATURE_COLUMNS[:5], FEATURE_COLUMNS])
@pytest.mark.parametrize('version', ['day1-logreg-v1', 'alpha-atlas-v1', 'alpha-atlas-v1-fallback'])
def test_day1_complete_partial_and_unavailable(tmp_path, columns, version):
    a = DeterministicQuickAdvisor(enabled=True, artifact_path=str(write_model(tmp_path, columns, version)))
    assert resolve_serving_feature_contract(a.artifact).classification == 'SUPPORTED_DAY1'
    complete = dict(technical={'rsi': 50, 'macd_histogram': .1}, volume_ratio=1, return_5d=.02)
    assert a.predict_quick_decision(signal_data=complete, quote_data={'change_percent': 1}) is not None
    partial = a.predict_quick_decision(signal_data={'volume_ratio': 1}, quote_data={})
    assert partial is not None and partial['imputed_features']
    assert a.predict_quick_decision(signal_data={}, quote_data={'price': 100}) is None


def test_reload_cannot_retain_previous_contract_and_logs_are_bounded(tmp_path, caplog):
    p = write_model(tmp_path, FEATURE_COLUMNS, 'alpha-atlas-v1')
    a = DeterministicQuickAdvisor(enabled=True, artifact_path=str(p))
    assert a.predict_quick_decision(signal_data={'volume_ratio': 1}, quote_data={})
    write_model(tmp_path, ALPHA_ATLAS_V3_FEATURES[:-1])
    assert a.reload_artifact()  # loaded remains distinct from servable
    for _ in range(5):
        assert a.predict_quick_decision(signal_data={}, quote_data={}) is None
    assert a.last_feature_contract_diagnostics['feature_contract_classification'] == 'UNSUPPORTED_SCHEMA'
    assert sum('unsupported_feature_contract' in r.message for r in caplog.records) == 1


def test_full_v3_preserves_serialized_order_and_existing_missing_gate(tmp_path, monkeypatch):
    cols = list(reversed(ALPHA_ATLAS_V3_FEATURES))
    a = DeterministicQuickAdvisor(enabled=True, artifact_path=str(write_model(tmp_path, cols, 'candidate-alpha-atlas-v3-clean-v1')))
    values = {f: float(i+1) for i, f in enumerate(ALPHA_ATLAS_V3_FEATURES)}
    monkeypatch.setattr('moneybot.services.deterministic_advisor.build_alpha_atlas_v3_features', lambda **kwargs: values)
    row, _, diag = a._build_feature_row_with_diagnostics({}, {})
    assert row.tolist() == [values[f] for f in cols]
    assert diag['feature_contract_servable']
    for f in cols[:3]: values[f] = None
    assert a.predict_quick_decision(signal_data={}, quote_data={}) is None
    values.update({f: float('nan') for f in cols})
    assert a.predict_quick_decision(signal_data={}, quote_data={}) is None
    assert a.last_feature_contract_diagnostics['feature_contract_reason'] == 'invalid_feature_vector'
