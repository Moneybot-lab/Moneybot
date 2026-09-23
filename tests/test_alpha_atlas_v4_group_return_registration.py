from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT=Path('docs/reports')
JSON_PATH=ROOT/'alpha_atlas_v4_group_return_regression_registration.v1.json'


def _registration():
    return json.loads(JSON_PATH.read_text())


def test_registration_is_complete_review_only_and_bound_to_accepted_review():
    data=_registration()
    assert data['status']=='PREPARED_FOR_REVIEW'
    assert data['implementation_status']=='NOT_AUTHORIZED_NOT_IMPLEMENTED'
    assert data['training_status']==data['execution_status']=='NOT_AUTHORIZED_NOT_EXECUTED'
    assert data['hypothesis']=='Training directly on equal-weight ticker/timing-group five-session returns may improve top-five gross endpoint selection relative to the eligible-cohort baseline.'
    assert data['positioning']['confirmatory'] is False and data['positioning']['prior_development_exposure'] is True
    assert data['positioning']['basis']['alignment_review_sha256']=='c8d3138c95ff9b5f84e7aafda1b08c8587605699a61e927426e362289b21274b'
    assert data['unresolved_design_choices']==[]
    assert not any(data['safety'].values())


def test_group_target_model_and_features_are_single_fixed_contract():
    data=_registration()
    assert data['group_contract']['key']==['event_date','ticker','label_horizon_sessions','entry_at','exit_at']
    assert data['aggregation']['multiplicity'].startswith('one group is one example')
    assert data['target']['type']=='continuous_float' and data['target']['prediction_semantics']=='predicted gross split-adjusted five-session group return'
    assert data['features']['count']==42==len(data['features']['list'])
    assert 'feature_close' not in data['features']['list'] and 'feature_close' in data['features']['excluded']
    assert data['model']['family']=='deterministic ridge linear regression'
    assert data['model']['alpha']==1.0 and data['model']['configuration_count']==1 and data['model']['searches'] is False
    assert data['model']['training_weight']==1.0


def test_split_selection_aggregation_and_screen_are_frozen():
    data=_registration(); folds=data['membership']['folds']
    assert [(x['fold'],x['train_rows'],x['validation_rows']) for x in folds]==[(1,11480,4464),(2,17856,2479),(3,11366,3330)]
    assert data['membership']['final_holdout']['content_access'] is False
    assert data['selection']['direction']=='descending' and data['selection']['k']==5
    assert data['selection']['predicted_return_cutoff'] is None
    assert data['evaluation']['fold_aggregation'].startswith('equal weight 1/3')
    assert 'NOT_EVALUABLE' in data['evaluation']['blocked_fold']
    assert data['development_screen']['aggregate_rule']=='selected_minus_baseline > 0'
    assert data['development_screen']['fold_rule']=='selected_minus_baseline > 0 in at least 2 of 3 folds'
    assert data['execution_budget']=={**data['execution_budget'],'candidate_configurations':1,'fold_fits':3,'final_holdout_fits':0,'hyperparameter_trials':0,'provider_calls':0}


def test_registration_checksum_manifest_excludes_itself_and_verifies():
    lines=(ROOT/'alpha_atlas_v4_group_return_regression_registration.v1.SHA256SUMS').read_text().splitlines()
    assert len(lines)==2 and not any(line.endswith('SHA256SUMS') for line in lines)
    for line in lines:
        expected,name=line.split('  ',1)
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected
    assert hashlib.sha256(JSON_PATH.read_bytes()).hexdigest()=='c3f58f47354f07a5c0aa536344b97fb542fdde0bee1651f86852d3dd69a6469a'
