import hashlib
import json
from pathlib import Path

ROOT=Path('docs/reports')
PROPOSAL=ROOT/'alpha_atlas_v4_ranking_cohort_relative_experiment_proposal.v1.json'

def test_proposal_is_complete_review_only_and_hash_pinned():
    data=json.loads(PROPOSAL.read_text())
    assert data['status']=='PREPARED_FOR_REVIEW'
    assert data['execution_status']=='NOT_AUTHORIZED_NOT_EXECUTED'
    assert data['selection_contract']['rule']=='fixed_top_k' and data['selection_contract']['k']==5
    assert data['scope_decision']['unresolved_design_choices']==[]
    assert data['safety']=={'training':False,'fitting':False,'tuning':False,'threshold_search':False,'k_search':False,'holdout_content_access':False,'provider_access':False,'execution_default':'OFF','requires_separate_review_and_authorization':True}
    assert hashlib.sha256(PROPOSAL.read_bytes()).hexdigest()=='000a0f8e2ba0fbaf563eb64dc3589fe4b2ae0695fd6c63fa8c536675d9b26f4d'

def test_roster_semantics_and_old_rule_are_preserved():
    data=json.loads(PROPOSAL.read_text()); roster={x['name']:x for x in data['candidate_roster']}
    assert set(roster)=={'challenger-ranking-lane-full-v1','challenger-ranking-lane-recent-half-v1','challenger-ranking-top5-model-v1'}
    assert roster['challenger-ranking-lane-full-v1']['score_semantics']=='ranking_score_not_buy_probability'
    assert roster['challenger-ranking-lane-recent-half-v1']['score_semantics']=='ranking_score_not_buy_probability'
    assert roster['challenger-ranking-top5-model-v1']['score_semantics']=='probability'
    assert data['positioning']['old_specification_unchanged'] is True
    assert data['positioning']['old_net_rule_status']=='REGISTERED_BLOCKED'

def test_selection_is_outcome_independent_and_missing_evidence_blocks():
    rule=json.loads(PROPOSAL.read_text())['selection_contract']
    assert rule['outcomes_used_for_selection'] is False
    assert 'block' in rule['missing_nonfinite']
    assert rule['tie_break']==['ticker ascending','sorted canonical_observation_id vector ascending']
    assert rule['identity_scope']=='ticker groups, not verified same-security identities'

def test_checksum_manifest_matches_every_report():
    for line in (ROOT/'alpha_atlas_v4_ranking_cohort_relative_experiment_proposal.v1.SHA256SUMS').read_text().splitlines():
        expected,name=line.split('  ',1)
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected

def test_closure_records_zero_exposure_not_success():
    data=json.loads((ROOT/'alpha_atlas_v4_ranking_zero_selection_closure.v1.json').read_text())
    assert data['status']=='COMPLETE' and data['classification']=='EXPECTED_FROZEN_ZERO_SELECTION'
    assert data['reconciliation']['assignments']==30819
    assert data['conclusions']['diagnostic_interpretation_defect'] is False
    assert data['conclusions']['corrected_scoring_required'] is False
    assert 'not successful stock selection' in data['conclusions']['gross_result_interpretation']
