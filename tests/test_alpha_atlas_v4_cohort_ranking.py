from __future__ import annotations
import hashlib, json, subprocess, sys
from pathlib import Path
import pytest
from scripts.audit_alpha_atlas_v4_cohort_ranking_inputs import failure_report, normalize_fold, validate_capture_scope, validate_membership_structure

from moneybot.services.alpha_atlas_v4_cohort_ranking import (
    CLARIFICATION_SHA256, EVIDENCE_SHA256, PROPOSAL_SHA256, RankingContractError,
    aggregate_folds, audit_selection_membership, compare_membership_evidence, complete_three_fold_aggregate,
    descriptive_returns_from_membership, score_candidate_fold, validate_execution_authorization, validate_frozen_contract,
)

CANDIDATE="challenger-ranking-lane-full-v1"

def row(identifier,ticker,score,ret,*,date="2025-01-02",horizon=5,entry="open",exit_at="close",label=1,**gates):
    return {"canonical_observation_id":identifier,"ticker":ticker,"event_date":date,"label_horizon_sessions":horizon,
        "entry_at":entry,"exit_at":exit_at,"score":score,"return":ret,"label":label,
        "abstained":False,"risk_rejected":False,"rule_rejected":False,**gates}

def test_fixed_top_five_signed_calculations_and_semantics():
    rows=[row(str(i),chr(65+i)*3,10-i,i/100,label=i%2) for i in range(6)]
    result=score_candidate_fold(rows,candidate=CANDIDATE,fold=1,training_labels=[0,1,0,0])
    assert result["score_semantics"]=="ranking_score_not_buy_probability"
    assert result["training_prevalence"]==.25 # fold training data only
    cohort=result["cohorts"][0]
    assert cohort["selected_tickers"]==["AAA","BBB","CCC","DDD","EEE"]
    assert cohort["selected_gross_return"]==pytest.approx(.02)
    assert cohort["eligible_baseline_return"]==pytest.approx(.025)
    assert cohort["signed_difference"]==pytest.approx(-.005)
    assert cohort["top_k_positive_label_precision"]==pytest.approx(.4)

def test_partial_pass_group_is_wholly_ineligible_and_denominator_is_all_members():
    rows=[row("a","AAA",1,.1),row("b","AAA",3,.3,risk_rejected=True),row("c","BBB",2,.2)]
    result=score_candidate_fold(rows,candidate=CANDIDATE,fold=1); cohort=result["cohorts"][0]
    aaa=next(x for x in cohort["groups"] if x["ticker"]=="AAA")
    assert aaa["score"]==2 and aaa["return"]==.2 and aaa["observations"]==2 and not aaa["eligible"]
    assert cohort["selected_tickers"]==["BBB"] and cohort["abstained_group_count"]==1
    assert result["group_eligibility_rule"]=="all_members_must_pass_saved_gates"

def test_multiplicity_does_not_change_equal_group_weight():
    rows=[row("a","AAA",3,.1),row("b","AAA",3,.3),row("c","BBB",2,.6)]
    cohort=score_candidate_fold(rows,candidate=CANDIDATE,fold=1)["cohorts"][0]
    assert cohort["selected_group_weight"]==.5
    assert cohort["selected_gross_return"]==pytest.approx(.4) # mean(.2,.6), not row mean

def test_incompatible_timings_are_separate_and_cohorts_equal_weighted():
    rows=[row("a","AAA",1,.2,entry="open"),row("b","BBB",1,.6,entry="noon")]
    result=score_candidate_fold(rows,candidate=CANDIDATE,fold=1)
    assert result["cohort_count"]==2 and result["cohort_weight"]==.5
    assert result["metrics"]["selected_gross_return"]==pytest.approx(.4)

def test_empty_selection_and_undefined_denominators_are_retained():
    result=score_candidate_fold([row("a","AAA",1,.2,abstained=True)],candidate=CANDIDATE,fold=1)
    cohort=result["cohorts"][0]
    assert cohort["empty_selection"] and cohort["selected_gross_return"]==0
    assert cohort["eligible_baseline_return"] is None and result["metric_denominators"]["signed_difference"]==0
    assert result["empty_cohort_count"]==1

def test_fold_aggregate_reconciliation_and_equal_defined_fold_weights():
    one=score_candidate_fold([row("a","AAA",1,.1)],candidate=CANDIDATE,fold=1)
    two=score_candidate_fold([row("b","AAA",1,.3)],candidate=CANDIDATE,fold=2)
    three=score_candidate_fold([row("c","AAA",1,.4,abstained=True)],candidate=CANDIDATE,fold=3)
    result=aggregate_folds([one,two,three])
    assert result["reconciles"] and result["metrics"]["selected_gross_return"]["value"]==pytest.approx(2/15)
    assert result["metrics"]["signed_difference"]["fold_weight"]==.5
    assert result["metrics"]["signed_difference"]["undefined_folds"]==[3]

@pytest.mark.parametrize("change,code",[("missing","MISSING_OR_NONFINITE_FIELD"),("null_gate","MISSING_REQUIRED_EVIDENCE"),("string_gate","MALFORMED_GATE"),("nan","MISSING_OR_NONFINITE_FIELD")])
def test_frozen_gates_and_malformed_values_fail_closed(change,code):
    value=row("a","AAA",1,.1)
    if change=="missing": value.pop("return")
    elif change=="null_gate": value["abstained"]=None
    elif change=="string_gate": value["abstained"]="false"
    else: value["score"]=float("nan")
    with pytest.raises(RankingContractError,match=code): score_candidate_fold([value],candidate=CANDIDATE,fold=1)

def _copy_pins(tmp_path):
    proposal=tmp_path/"proposal.json"; evidence=tmp_path/"evidence.json"
    proposal.write_bytes(Path("docs/reports/alpha_atlas_v4_ranking_cohort_relative_experiment_proposal.v1.json").read_bytes())
    evidence.write_bytes(Path("docs/reports/alpha_atlas_v4_narrow_frozen_sample_diagnostic.v1.1.json").read_bytes())
    lineage=tmp_path/"lineage.json"; lineage.write_text(json.dumps({"completed_diagnostic":{"run":"35795768048-1"},"approved_grouping_audit":{"run":"35788348286-1"},"source_registration":"35755237312-1","specification_sha256":EVIDENCE_SHA256}))
    clarification=tmp_path/"clarification.json"; clarification.write_bytes(Path("docs/reports/alpha_atlas_v4_cohort_relative_ranking_clarification.v1.json").read_bytes())
    return proposal,evidence,lineage,clarification

def test_exact_proposal_evidence_hashes_and_lineage_validate(tmp_path):
    proposal,evidence,lineage,clarification=_copy_pins(tmp_path)
    assert hashlib.sha256(proposal.read_bytes()).hexdigest()==PROPOSAL_SHA256
    assert hashlib.sha256(clarification.read_bytes()).hexdigest()==CLARIFICATION_SHA256
    assert validate_frozen_contract(proposal,evidence,lineage,clarification)["lineage"]["diagnostic_run"]=="35795768048-1"

@pytest.mark.parametrize("which,code",[("proposal","FROZEN_HASH_MISMATCH"),("evidence","FROZEN_HASH_MISMATCH"),("lineage","AUDIT_LINEAGE_MISMATCH")])
def test_mismatched_hashes_and_audit_lineage_fail_closed(tmp_path,which,code):
    proposal,evidence,lineage,clarification=_copy_pins(tmp_path)
    if which=="lineage": lineage.write_text("{}")
    else: {"proposal":proposal,"evidence":evidence}[which].write_text("{}")
    with pytest.raises(RankingContractError,match=code): validate_frozen_contract(proposal,evidence,lineage,clarification)

def test_malformed_json_and_cli_real_scoring_gate_emit_audit(tmp_path):
    proposal,evidence,lineage,clarification=_copy_pins(tmp_path); lineage.write_text("{")
    with pytest.raises(RankingContractError,match="MALFORMED_OR_MISSING_JSON"): validate_frozen_contract(proposal,evidence,lineage,clarification)
    _,_,lineage,clarification=_copy_pins(tmp_path); output=tmp_path/"audit.json"
    command=[sys.executable,"-m","scripts.validate_alpha_atlas_v4_cohort_ranking","--proposal",str(proposal),"--clarification",str(clarification),"--evidence",str(evidence),"--lineage",str(lineage),"--output",str(output),"--request-real-scoring"]
    env={k:v for k,v in __import__('os').environ.items() if k!='PYTHONPATH'}
    completed=subprocess.run(command,text=True,capture_output=True,env=env)
    report=json.loads(output.read_text())
    assert completed.returncode==2 and report["reason_code"]=="REAL_SCORING_HARD_DISABLED_REQUIRES_CODE_CHANGE"
    assert report["scoring"]=="NOT_RUN" and report["real_scoring_enabled"] is False

def test_outcome_free_ties_are_deterministic_and_input_order_invariant():
    records=[{k:v for k,v in item.items() if k not in ("return","label")} for item in [
        row("z","BBB",1,.9,label=0),row("a","AAA",1,-.9,label=1),row("c","CCC",.5,.2)]]
    first=audit_selection_membership(records,candidate=CANDIDATE,fold=1)
    second=audit_selection_membership(list(reversed(records)),candidate=CANDIDATE,fold=1)
    selected=lambda result:[(x["ticker"],x["rank"],x["selected_group_weight"]) for x in result["groups"]]
    assert selected(first)==selected(second)
    assert next(x for x in first["cohorts"])["tie_group_count"]==2
    changed=[{**item,"label":999,"return":-999} for item in records]
    assert selected(first)==selected(audit_selection_membership(changed,candidate=CANDIDATE,fold=1))

def test_outcome_free_partial_pass_empty_and_separate_windows():
    records=[{k:v for k,v in item.items() if k not in ("return","label")} for item in [
      row("a","AAA",2,.1),row("b","AAA",4,.2,rule_rejected=True),
      row("c","BBB",3,.3,entry="later",abstained=True)]]
    result=audit_selection_membership(records,candidate=CANDIDATE,fold=2)
    assert len(result["cohorts"])==2 and all(x["empty"] for x in result["cohorts"])
    aaa=next(x for x in result["groups"] if x["ticker"]=="AAA")
    assert aaa["mean_score"]==3 and aaa["eligibility_classification"]=="partially_passing"
    assert aaa["within_group_observation_weight"]==.5

def test_altered_clarification_hash_is_rejected(tmp_path):
    proposal,evidence,lineage,clarification=_copy_pins(tmp_path); clarification.write_text("{}")
    with pytest.raises(RankingContractError,match="CLARIFICATION_HASH_MISMATCH"):
        validate_frozen_contract(proposal,evidence,lineage,clarification)

def test_input_audit_workflow_is_manual_read_only_and_no_performance():
    text=Path('.github/workflows/v4-cohort-relative-ranking.yml').read_text()
    assert text.startswith('name: V4 Cohort Relative Ranking Input Audit\n')
    assert 'workflow_dispatch:' in text and 'push:' not in text and 'schedule:' not in text
    assert 'actions: read' in text and 'contents: read' in text
    assert 'audit_alpha_atlas_v4_cohort_ranking_inputs' in text
    assert '34689216730' in text and '34769717178' in text and CLARIFICATION_SHA256 not in text
    assert 'request-real-scoring' not in text and 'train_challenger' not in text
    audit_source=Path('scripts/audit_alpha_atlas_v4_cohort_ranking_inputs.py').read_text()
    assert 'score_candidate_fold' not in audit_source and 'performance_scoring":"NOT_RUN"' in audit_source

def test_assignment_fold_and_holdout_contracts_fail_closed():
    expected={1:{"a","b"},2:{"c"},3:{"d"}}
    counts={1:2,2:1,3:1}
    captures={(candidate,fold):sorted(expected[fold]) for candidate in (
      "challenger-ranking-lane-full-v1","challenger-ranking-lane-recent-half-v1","challenger-ranking-top5-model-v1") for fold in counts}
    validate_membership_structure(expected,captures,set(),expected_counts=counts)
    cases=[]
    cases.append(({1:expected[1],2:expected[2]},captures,set(),"MISSING_OR_UNEXPECTED_FOLDS"))
    duplicate={**captures,(CANDIDATE,1):["a","a"]}; cases.append((expected,duplicate,set(),"DUPLICATE_CAPTURE_ASSIGNMENT"))
    missing={key:value for key,value in captures.items() if key!=(CANDIDATE,3)}; cases.append((expected,missing,set(),"CAPTURE_CANDIDATE_FOLD_SET_MISMATCH"))
    cases.append((expected,captures,{"c"},"HOLDOUT_MEMBERSHIP_OVERLAP"))
    for folds,observed,holdout,code in cases:
        with pytest.raises(RankingContractError,match=code): validate_membership_structure(folds,observed,holdout,expected_counts=counts)

def test_input_audit_exact_module_entrypoint_without_pythonpath_preserves_failure(tmp_path):
    proposal,evidence,lineage,clarification=_copy_pins(tmp_path)
    provenance=tmp_path/'provenance.json'; provenance.write_text('{}'); output=tmp_path/'output'
    missing=tmp_path/'missing'
    command=[sys.executable,'-m','scripts.audit_alpha_atlas_v4_cohort_ranking_inputs',
      '--proposal',str(proposal),'--clarification',str(clarification),'--evidence',str(evidence),'--lineage',str(lineage),
      '--canonical',str(missing),'--plan',str(missing),'--manifest',str(missing),'--capture',str(missing),
      '--input-provenance',str(provenance),'--output-dir',str(output)]
    env={k:v for k,v in __import__('os').environ.items() if k!='PYTHONPATH'}
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
    report=json.loads((output/'cohort_ranking_input_audit.json').read_text())
    assert completed.returncode==2 and report['status']=='AUDIT_FAILED_NO_PERFORMANCE'
    assert report['performance_scoring']=='NOT_RUN' and report['holdout_content_access'] is False
    assert (output/'SHA256SUMS').is_file() and (output/clarification.name).is_file()
    checks=(output/'SHA256SUMS').read_text().splitlines()
    assert checks and not any(line.endswith('  SHA256SUMS') for line in checks)

def _capture_block(candidate,fold):
    return {'model_version':candidate,'fold_index':fold,'records':[]}

def test_full_capture_legitimate_out_of_scope_candidates_are_validated_then_projected():
    authorized=list(__import__('moneybot.services.alpha_atlas_v4_cohort_ranking',fromlist=['CANDIDATES']).CANDIDATES)
    manifest=authorized+['legitimate-other-candidate']
    capture=[_capture_block(candidate,fold) for candidate in manifest for fold in (1,2,3)]
    projected,diagnostics=validate_capture_scope(manifest,capture,{1,2,3})
    assert set(projected)=={(candidate,fold) for candidate in authorized for fold in (1,2,3)}
    assert diagnostics['outside_experiment_scope_candidates']==['legitimate-other-candidate']
    assert len(diagnostics['observed_source_candidate_fold_pairs'])==12

def test_capture_scope_reports_missing_authorized_fold_and_unknown_source_candidate():
    authorized=list(__import__('moneybot.services.alpha_atlas_v4_cohort_ranking',fromlist=['CANDIDATES']).CANDIDATES)
    complete=[_capture_block(candidate,fold) for candidate in authorized for fold in (1,2,3)]
    with pytest.raises(RankingContractError,match='MISSING_SOURCE_CANDIDATE_FOLD') as missing:
        validate_capture_scope(authorized,complete[:-1],{1,2,3})
    assert missing.value.details['missing_source_pairs'] and missing.value.details['missing_authorized_pairs']
    with pytest.raises(RankingContractError,match='UNKNOWN_SOURCE_CANDIDATE_OR_FOLD') as unknown:
        validate_capture_scope(authorized,complete+[_capture_block('unknown',1)],{1,2,3})
    assert unknown.value.details['unexpected_source_pairs']==[['unknown',1]]

def test_fold_representation_normalization_rejects_invalid_and_collisions():
    authorized=list(__import__('moneybot.services.alpha_atlas_v4_cohort_ranking',fromlist=['CANDIDATES']).CANDIDATES)
    capture=[_capture_block(candidate,str(fold)) for candidate in authorized for fold in (1,2,3)]
    projected,diagnostics=validate_capture_scope(authorized,capture,{1,2,3})
    assert len(projected)==9 and diagnostics['fold_value_types']=={'str':9}
    with pytest.raises(RankingContractError,match='DUPLICATE_CANDIDATE_FOLD_CAPTURE') as collision:
        validate_capture_scope(authorized,capture+[_capture_block(authorized[0],1)],{1,2,3})
    assert collision.value.details['duplicate_pairs'][0]['representations']==["'1'",'1']
    for value in (True,1.0,'one',None):
        with pytest.raises(RankingContractError,match='INVALID_FOLD_REPRESENTATION'): normalize_fold(value)

def test_failure_after_byte_verification_preserves_input_provenance():
    provenance={'capture':{'computed_sha256':'abc','bytes':123}}
    error=RankingContractError('CAPTURE_CANDIDATE_FOLD_SET_MISMATCH',{
      'input_verification_status':'PASSED','input_provenance':provenance,'missing_authorized_pairs':[['candidate',3]]})
    report=failure_report(error)
    assert report['input_verification_status']=='PASSED'
    assert report['candidate_fold_membership_verification_status']=='FAILED'
    assert report['input_provenance']==provenance and report['details']['missing_authorized_pairs']

def _approved_membership_fixture():
    records=[{k:v for k,v in item.items() if k not in ('return','label')} for item in [
      row('a','AAA',3,.1),row('b','AAA',1,.3),row('c','BBB',2,.6)]]
    return audit_selection_membership(records,candidate=CANDIDATE,fold=1)

def test_registered_outcome_arithmetic_uses_group_then_cohort_weights():
    membership=_approved_membership_fixture()
    result=descriptive_returns_from_membership(membership,{'a':.1,'b':.3,'c':.6},candidate=CANDIDATE,fold=1)
    assert result['metrics']['selected_gross_endpoint_return']==pytest.approx(.4)
    assert result['metrics']['eligible_baseline_gross_endpoint_return']==pytest.approx(.4)
    assert result['metrics']['selected_minus_baseline']==pytest.approx(0)
    assert result['cohorts'][0]['selected_member_observation_count']==3

def test_outcome_changes_do_not_change_audited_membership_and_missing_blocks():
    membership=_approved_membership_fixture(); before=json.dumps(membership,sort_keys=True)
    descriptive_returns_from_membership(membership,{'a':-10,'b':20,'c':-30},candidate=CANDIDATE,fold=1)
    assert json.dumps(membership,sort_keys=True)==before
    with pytest.raises(RankingContractError,match='MISSING_OR_NONFINITE_OUTCOME'):
        descriptive_returns_from_membership(membership,{'a':.1,'b':.2},candidate=CANDIDATE,fold=1)

def test_membership_identity_and_weights_must_match_exactly():
    membership=_approved_membership_fixture(); reproduced=json.loads(json.dumps(membership))
    assert compare_membership_evidence(membership,reproduced)['rows']['approved_count']==3
    reproduced['rows'][0]['selected_group_weight']+=.01
    with pytest.raises(RankingContractError,match='AUDITED_MEMBERSHIP_MISMATCH'):
        compare_membership_evidence(membership,reproduced)

def test_aggregate_requires_all_three_folds_and_preserves_direction():
    folds=[]
    for fold,value in ((1,.1),(2,-.2),(3,.3)):
      folds.append({'fold':fold,'status':'COMPLETE','metrics':{'selected_gross_endpoint_return':value,
        'eligible_baseline_gross_endpoint_return':0,'selected_minus_baseline':value,'selected_minus_cash':value}})
    result=complete_three_fold_aggregate(folds)
    assert result['status']=='COMPLETE' and result['fold_weight']==pytest.approx(1/3)
    assert result['metrics']['selected_minus_baseline']==pytest.approx(2/30)
    assert [x['direction'] for x in result['fold_directions']]==['POSITIVE','NEGATIVE','POSITIVE']
    assert complete_three_fold_aggregate(folds[:2])['status']=='NOT_EVALUABLE_INCOMPLETE_FOLDS'

def test_changed_or_unauthorized_execution_hash_fails_closed(tmp_path,monkeypatch):
    import moneybot.services.alpha_atlas_v4_cohort_ranking as service
    authorization=tmp_path/'authorization'; authorization.write_text(json.dumps({'status':'AUTHORIZED','approved_input_audit':{'run':'35895423660-1','status':'AUDIT_COMPLETE_NO_PERFORMANCE'}}))
    audit=tmp_path/'audit'; audit.write_text(json.dumps({'status':'AUDIT_COMPLETE_NO_PERFORMANCE','observed_total_assignments':30819,'holdout_membership_overlap_count':0}))
    membership=tmp_path/'membership'; membership.write_text('{}')
    monkeypatch.setattr(service,'AUTHORIZED_EXECUTION_SHA256',hashlib.sha256(authorization.read_bytes()).hexdigest())
    monkeypatch.setattr(service,'APPROVED_AUDIT_REPORT_SHA256',hashlib.sha256(audit.read_bytes()).hexdigest())
    monkeypatch.setattr(service,'APPROVED_MEMBERSHIP_SHA256',hashlib.sha256(membership.read_bytes()).hexdigest())
    assert validate_execution_authorization(authorization,audit,membership)['status']=='AUTHORIZED'
    membership.write_text('{"changed":true}')
    with pytest.raises(RankingContractError,match='EXECUTION_AUTHORIZATION_OR_AUDIT_HASH_MISMATCH'):
        validate_execution_authorization(authorization,audit,membership)

def test_scoring_workflow_and_entrypoint_are_separate_and_manual():
    workflow=Path('.github/workflows/v4-execute-cohort-relative-ranking.yml').read_text()
    assert workflow.startswith('name: V4 Execute Cohort Relative Ranking\n')
    assert 'workflow_dispatch:' in workflow and 'push:' not in workflow and 'schedule:' not in workflow
    assert 'actions: read' in workflow and 'contents: read' in workflow
    assert '35895423660' in workflow and 'execute_alpha_atlas_v4_cohort_ranking' in workflow
    assert 'inputs:' not in workflow and 'train_challenger' not in workflow
    validation=Path('scripts/validate_alpha_atlas_v4_cohort_ranking.py').read_text()
    assert 'execute_alpha_atlas_v4_cohort_ranking' not in validation

def test_execution_exact_module_entrypoint_without_pythonpath_fails_closed(tmp_path):
    output=tmp_path/'output'; missing=tmp_path/'missing'
    command=[sys.executable,'-m','scripts.execute_alpha_atlas_v4_cohort_ranking']
    for name in ('authorization','proposal','clarification','evidence','lineage','approved-audit','approved-membership','canonical','plan','manifest','capture','input-provenance'):
        command += ['--'+name,str(missing)]
    command += ['--output-dir',str(output)]
    env={k:v for k,v in __import__('os').environ.items() if k!='PYTHONPATH'}
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
    result=json.loads((output/'cohort_ranking_results.json').read_text())
    assert completed.returncode==2 and result['execution_status']=='FAILED'
    assert result['holdout_content_access'] is False and result['winner_selected'] is False

def test_recorded_authorized_result_is_complete_nonpromotional_and_reconciled():
    path=Path('docs/reports/alpha_atlas_v4_cohort_relative_ranking_results.v1.json'); result=json.loads(path.read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='ad73d14b5dfb11b00bc6df3978b2bfdcb015314413f67e2eae32493bb8a116e9'
    assert result['execution_status']=='COMPLETE' and result['winner_selected'] is False
    assert result['holdout_content_access'] is False and result['portfolio_curve_computed'] is False
    assert result['outcome_semantics']['missing_or_nonfinite_count']==0
    assert result['candidate_order']==list(__import__('moneybot.services.alpha_atlas_v4_cohort_ranking',fromlist=['CANDIDATES']).CANDIDATES)
    for candidate in result['candidates']:
        assert [fold['fold'] for fold in candidate['folds']]==[1,2,3]
        assert all(fold['cohort_count']==26 for fold in candidate['folds'])
        assert all(fold['metrics']['selected_minus_baseline']<0 for fold in candidate['folds'])
        assert candidate['secondary_label_metrics']['status']=='NOT_EVALUABLE'
