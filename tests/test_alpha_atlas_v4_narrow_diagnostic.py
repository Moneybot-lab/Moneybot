from __future__ import annotations
import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest
from moneybot.services.alpha_atlas_v4_narrow_diagnostic import DiagnosticSpecError, aggregate_candidate, compare_grouping_reproduction, descriptive_metrics, equal_fold_aggregate, grouping_audit, normalize_multiplicity_histogram, score_candidate_fold, validate_spec
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash


def _row(identifier,ticker,date,horizon=5,entry="2023-01-03T14:30:00+00:00",exit_at="2023-01-10T21:00:00+00:00"):
    return {"canonical_observation_id":identifier,"symbol":ticker,"event_date":date,"label_horizon_sessions":horizon,"entry_at":entry,"exit_at":exit_at}

def _write_spec(path,inputs,audit_contract=None):
    frozen={role:{"file_byte_sha256":hashlib.sha256(source.read_bytes()).hexdigest()} for role,source in inputs.items()}
    plan=json.loads(inputs['plan'].read_text()); frozen['plan'].update(semantic_content_sha256=plan['plan_sha256'],semantic_content_hash_algorithm='canonical_json_hash_without_plan_sha256')
    payload={"frozen_inputs":frozen,"execution":{"default":"OFF"},"broader_registration":{"status":"REGISTERED_BLOCKED"},"source_registration":{"run":"fixture"},"claim_limits":[]}
    if audit_contract: payload['approved_grouping_audit']=audit_contract
    path.write_text(json.dumps(payload))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ticker_date_groups_equal_weight_despite_unequal_rows_and_reconcile():
    rows=[_row('a','AAA','2023-01-02'),_row('b','AAA','2023-01-02'),_row('c','BBB','2023-01-02')]
    assignments=[{"candidate":"c1","fold":1,"canonical_observation_id":x["canonical_observation_id"]} for x in rows]
    audit=grouping_audit(rows,assignments); weights={x['canonical_observation_id']:x['final_cohort_row_weight'] for x in audit['row_to_group_mapping']}
    assert weights=={'a':.25,'b':.25,'c':.5}
    assert audit['multiple_observation_groups']==1 and audit['weights_reconcile_per_cohort'] is True
    assert audit['security_identity_claimed'] is False


def test_horizons_and_execution_windows_form_separate_cohorts():
    rows=[_row('a','AAA','2023-01-02'),_row('b','AAA','2023-01-02',horizon=10),
          _row('c','AAA','2023-01-02',entry="2023-01-04T14:30:00+00:00")]
    assignments=[{"candidate":"c1","fold":1,"canonical_observation_id":x["canonical_observation_id"]} for x in rows]
    assert grouping_audit(rows,assignments)['cohorts']==3


def test_abstention_no_selection_and_undefined_denominators_are_retained():
    result=descriptive_metrics([{"label":0,"prediction":False,"abstained":True},{"label":0,"prediction":False,"abstained":False}])
    assert result['denominator']==2 and result['selected']==0 and result['abstained']==1
    assert result['coverage']==0 and result['precision'] is None and result['recall'] is None
    assert result['undefined']=={'precision':'NO_SELECTED_POSITIVES','recall':'NO_POSITIVE_LABELS'}


def test_changed_specification_or_input_hash_is_rejected(tmp_path):
    inputs={};
    for role in ('canonical','manifest','capture'):
        path=tmp_path/role; path.write_text(role); inputs[role]=path
    core={'train_canonical_observation_ids':['a']}; plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':canonical_json_hash(core)})); inputs['plan']=plan
    spec=tmp_path/'spec.json'; reviewed=_write_spec(spec,inputs); validate_spec(spec,reviewed,inputs)
    with pytest.raises(DiagnosticSpecError,match='REVIEWED_SPECIFICATION_HASH_MISMATCH'): validate_spec(spec,'0'*64,inputs)
    inputs['capture'].write_text('changed')
    with pytest.raises(DiagnosticSpecError,match='FROZEN_INPUT_HASH_MISMATCH') as error: validate_spec(spec,reviewed,inputs)
    assert error.value.role=='capture' and error.value.hash_type=='file_byte_sha256'

def test_plan_byte_and_content_hashes_are_distinct_and_both_enforced(tmp_path):
    core={'train_canonical_observation_ids':['a'],'test_canonical_observation_ids':['h']}; content=canonical_json_hash(core)
    plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':content},indent=2)+'\n')
    inputs={role:tmp_path/role for role in ('canonical','manifest','capture')}; [p.write_text(p.name) for p in inputs.values()]; inputs['plan']=plan
    spec=tmp_path/'spec'; reviewed=_write_spec(spec,inputs); assert hashlib.sha256(plan.read_bytes()).hexdigest()!=content
    validate_spec(spec,reviewed,inputs)
    # Formatting alone preserves semantic content but violates the exact-byte pin.
    plan.write_text(json.dumps({**core,'plan_sha256':content},separators=(',',':')))
    with pytest.raises(DiagnosticSpecError) as formatting: validate_spec(spec,reviewed,inputs)
    assert formatting.value.hash_type=='file_byte_sha256'

def test_changed_plan_content_or_embedded_hash_fails_semantic_validation(tmp_path):
    core={'train_canonical_observation_ids':['a']}; content=canonical_json_hash(core); plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':content}))
    inputs={role:tmp_path/role for role in ('canonical','manifest','capture')}; [p.write_text(p.name) for p in inputs.values()]; inputs['plan']=plan
    spec=tmp_path/'spec'
    changed={**core,'train_canonical_observation_ids':['b'],'plan_sha256':content}; plan.write_text(json.dumps(changed)); reviewed=_write_spec(spec,inputs)
    with pytest.raises(DiagnosticSpecError,match='SPLIT_PLAN_CONTENT_HASH_MISMATCH'): validate_spec(spec,reviewed,inputs)
    plan.write_text(json.dumps({**core,'plan_sha256':'0'*64})); reviewed=_write_spec(spec,inputs)
    payload=json.loads(spec.read_text()); payload['frozen_inputs']['plan']['semantic_content_sha256']=content; spec.write_text(json.dumps(payload)); reviewed=hashlib.sha256(spec.read_bytes()).hexdigest()
    with pytest.raises(DiagnosticSpecError,match='SPLIT_PLAN_EMBEDDED_HASH_MISMATCH'): validate_spec(spec,reviewed,inputs)


def test_duplicate_assignment_rejected():
    row=_row('a','AAA','2023-01-02'); assignment={"candidate":"c1","fold":1,"canonical_observation_id":"a"}
    with pytest.raises(DiagnosticSpecError,match='DUPLICATE_CANDIDATE_FOLD_ASSIGNMENT'):
        grouping_audit([row],[assignment,assignment])


def test_fold_aggregation_is_equal_weight_and_retains_undefined_fold():
    result=equal_fold_aggregate({1:.1,2:.3,3:None})
    assert result=={'value':.2,'defined_folds':[1,2],'undefined_folds':[3],'weight_per_defined_fold':.5}


def test_audit_entrypoint_runs_membership_and_grouping_without_scoring(tmp_path):
    canonical=tmp_path/'canonical.jsonl'; row=_row('a','AAA','2023-01-02'); canonical.write_text(json.dumps(row)+'\n')
    plan=tmp_path/'plan.json'; core={'train_canonical_observation_ids':['a'],'test_canonical_observation_ids':['holdout-id']}; plan.write_text(json.dumps({**core,'plan_sha256':canonical_json_hash(core)},indent=2)+'\n')
    manifest=tmp_path/'manifest.json'; manifest.write_text('{}')
    capture=tmp_path/'capture.json'; capture.write_text(json.dumps([{'model_version':'c1','fold_index':1,'records':[{'id':'a'}]}]))
    inputs={'canonical':canonical,'plan':plan,'manifest':manifest,'capture':capture}
    spec=tmp_path/'spec.json'; reviewed=_write_spec(spec,inputs); out=tmp_path/'out'; env=os.environ.copy(); env.pop('PYTHONPATH',None)
    command=[sys.executable,'-m','scripts.audit_alpha_atlas_v4_narrow_diagnostic','--specification',str(spec),'--reviewed-specification-hash',reviewed,
      '--canonical',str(canonical),'--plan',str(plan),'--manifest',str(manifest),'--capture',str(capture),'--output-dir',str(out)]
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
    assert completed.returncode==0,completed.stderr
    audit=json.loads((out/'narrow_diagnostic_grouping_audit.json').read_text())
    assert audit['status']=='AUDIT_COMPLETE_NO_SCORING' and audit['performance_scoring_executed'] is False
    assert audit['holdout_content_loaded'] is False and audit['weights_reconcile_per_cohort'] is True
    assert (out/'reviewed_narrow_diagnostic_specification.json').read_bytes()==spec.read_bytes()

def test_audit_entrypoint_preserves_structured_hash_failure(tmp_path):
    canonical=tmp_path/'canonical'; canonical.write_text(json.dumps(_row('a','AAA','2023-01-02'))+'\n')
    core={'train_canonical_observation_ids':['a'],'test_canonical_observation_ids':['h']}; plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':canonical_json_hash(core)}))
    manifest=tmp_path/'manifest'; manifest.write_text('{}'); capture=tmp_path/'capture'; capture.write_text('[]')
    inputs={'canonical':canonical,'plan':plan,'manifest':manifest,'capture':capture}; spec=tmp_path/'spec'; reviewed=_write_spec(spec,inputs)
    plan.write_text(plan.read_text()+'\n'); out=tmp_path/'out'
    command=[sys.executable,'-m','scripts.audit_alpha_atlas_v4_narrow_diagnostic','--specification',str(spec),'--reviewed-specification-hash',reviewed,
      '--canonical',str(canonical),'--plan',str(plan),'--manifest',str(manifest),'--capture',str(capture),'--output-dir',str(out)]
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True)
    assert completed.returncode==2
    failure=json.loads((out/'narrow_diagnostic_grouping_audit.json').read_text())
    assert failure['input_role']=='plan' and failure['hash_type']=='file_byte_sha256'
    assert failure['expected_hash'] and failure['actual_hash'] and not failure['grouping_executed'] and not failure['performance_scoring_executed']

def test_probability_baseline_uses_only_fold_training_partition_and_signed_direction():
    rows={'train0':{**_row('train0','AAA','2023-01-01'),'label_up_5d':0,'return_5d':0},
          'train1':{**_row('train1','BBB','2023-01-01'),'label_up_5d':1,'return_5d':0},
          'v':{**_row('v','CCC','2023-01-02'),'label_up_5d':1,'return_5d':.1}}
    item={'model_version':'candidate','fold_index':1,'decision_threshold':.6,'score_semantics':'probability','target_definition':{'name':'label_up_5d','horizon_sessions':5},
      'train_ids':['train0','train1'],'records':[{'id':'v','score':.8,'label':1,'return':.1}]}
    mapping=grouping_audit([rows['v']],[{'candidate':'candidate','fold':1,'canonical_observation_id':'v'}])['row_to_group_mapping']
    result=score_candidate_fold(item,rows,mapping)
    assert result['training_prevalence']==.5 and result['denominators']['training_prevalence_rows']==2
    assert result['probability']['brier']==pytest.approx(.04) and result['probability']['prevalence_brier']==.25
    assert result['probability']['signed_difference']<0
    assert result['probability']['calibration']=='NOT_EVALUABLE_CALIBRATION_BIN_COUNT_UNSPECIFIED'

def test_gross_weighting_partial_selection_and_empty_cohort_are_cash_not_dropped():
    rows={'a':{**_row('a','AAA','2023-01-02'),'label_up_5d':1,'return_5d':.1},
          'b':{**_row('b','AAA','2023-01-02'),'label_up_5d':0,'return_5d':-.1},
          'c':{**_row('c','BBB','2023-01-02'),'label_up_5d':1,'return_5d':.2}}
    assignments=[{'candidate':'c1','fold':1,'canonical_observation_id':x} for x in rows]
    mapping=grouping_audit(list(rows.values()),assignments)['row_to_group_mapping']
    item={'model_version':'c1','fold_index':1,'candidate_type':'ranking','decision_threshold':.5,'score_semantics':'probability','target_definition':{'name':'label_up_5d'},'train_ids':['a','b'],
      'records':[{'id':'a','score':.9,'label':1,'return':.1},{'id':'b','score':.1,'label':0,'return':-.1},{'id':'c','score':.9,'label':1,'return':.2,'abstained':True}]}
    result=score_candidate_fold(item,rows,mapping)
    # AAA has half its group selected: .25*.1; BBB abstains and contributes cash zero.
    assert result['gross_endpoint']['candidate']==pytest.approx(.025)
    assert result['gross_endpoint']['equal_weight_ticker_date_timing_baseline']==pytest.approx(.1)
    assert result['gross_endpoint']['signed_difference']==pytest.approx(-.075)
    assert result['denominators']['empty_selection_cohorts']==0  # cohort has one selected AAA observation
    assert result['classification']['abstained']==1

def test_candidate_aggregate_equal_weights_folds_and_preserves_undefined():
    folds=[{'fold':1,'probability':{'brier':.1,'signed_difference':-.1},'classification':{'precision':.5,'recall':.5,'coverage':.5},'gross_endpoint':{'candidate':.02,'signed_difference':.01}},
           {'fold':2,'probability':{'brier':.3,'signed_difference':.1},'classification':{'precision':None,'recall':.4,'coverage':0},'gross_endpoint':{'candidate':0,'signed_difference':-.01}}]
    aggregate=aggregate_candidate(folds)
    assert aggregate['probability.brier']['value']==.2
    assert aggregate['classification.precision']['defined_folds']==[1] and aggregate['classification.precision']['undefined_folds']==[2]

def test_execution_entrypoint_fails_closed_on_unapproved_audit_without_scoring(tmp_path):
    canonical=tmp_path/'canonical'; canonical.write_text(json.dumps(_row('a','AAA','2023-01-02'))+'\n')
    core={'train_canonical_observation_ids':['a'],'test_canonical_observation_ids':['h']}; plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':canonical_json_hash(core)}))
    manifest=tmp_path/'manifest'; manifest.write_text(json.dumps({'challengers':[]})); capture=tmp_path/'capture'; capture.write_text('[]')
    inputs={'canonical':canonical,'plan':plan,'manifest':manifest,'capture':capture}; spec=tmp_path/'spec'; reviewed=_write_spec(spec,inputs)
    audit=tmp_path/'audit'; audit.write_text(json.dumps({'status':'AUDIT_COMPLETE_NO_SCORING','specification_sha256':reviewed,'rows':1,'assignments':0,'weights_reconcile_per_cohort':True,'performance_scoring_executed':False}))
    out=tmp_path/'out'; command=[sys.executable,'-m','scripts.execute_alpha_atlas_v4_narrow_diagnostic','--specification',str(spec),'--reviewed-specification-hash',reviewed,
      '--canonical',str(canonical),'--plan',str(plan),'--manifest',str(manifest),'--capture',str(capture),'--approved-grouping-audit',str(audit),'--output-dir',str(out)]
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True)
    assert completed.returncode==2
    result=json.loads((out/'narrow_diagnostic_results.json').read_text())
    assert result['reason_code']=='APPROVED_GROUPING_AUDIT_IDENTITY_MISMATCH' and result['scoring_completed'] is False
    assert (out/'execution_provenance.json').is_file() and (out/'SHA256SUMS').is_file()

def test_manual_execution_workflow_is_pinned_read_only_and_not_automatic():
    text=Path('.github/workflows/v4-execute-narrow-frozen-sample-diagnostic.yml').read_text()
    assert text.startswith('name: V4 Execute Narrow Frozen Sample Diagnostic\n')
    assert 'workflow_dispatch:' in text and 'push:' not in text and 'schedule:' not in text
    assert 'actions: read' in text and 'contents: read' in text
    assert '35788348286' in text and '206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2' in text
    assert 'python -m scripts.execute_alpha_atlas_v4_narrow_diagnostic' in text
    assert 'train_challenger' not in text and 'MASSIVE_API_KEY' not in text

def test_json_round_trip_histogram_normalizes_representation_only():
    rows=[_row('a','AAA','2023-01-02'),_row('b','AAA','2023-01-02')]
    assignments=[{'candidate':'c1','fold':1,'canonical_observation_id':x['canonical_observation_id']} for x in rows]
    reproduced=grouping_audit(rows,assignments); approved=json.loads(json.dumps(reproduced))
    diagnostics=compare_grouping_reproduction(approved,reproduced)
    assert diagnostics['approved_key_types']==['str'] and diagnostics['reproduced_key_types']==['int']
    assert diagnostics['representation_only'] is True and not diagnostics['missing_multiplicities'] and not diagnostics['extra_multiplicities']

@pytest.mark.parametrize('histogram,code',[
    ({'2':2},'GROUPING_AUDIT_REPRODUCTION_MISMATCH:groups_by_row_multiplicity'),
    ({'1':2},'GROUPING_AUDIT_REPRODUCTION_MISMATCH:groups_by_row_multiplicity'),
    ({},'GROUPING_AUDIT_REPRODUCTION_MISMATCH:groups_by_row_multiplicity'),
    ({'2':1,'3':1},'GROUPING_AUDIT_REPRODUCTION_MISMATCH:groups_by_row_multiplicity'),
    ({'2':True},'MALFORMED_MULTIPLICITY_COUNT'),
    ({'2':1.5},'MALFORMED_MULTIPLICITY_COUNT'),
    ({'0':1},'MALFORMED_MULTIPLICITY_KEY'),
])
def test_histogram_gate_rejects_altered_missing_extra_or_invalid(histogram,code):
    actual={'rows':2,'assignments':2,'ticker_date_timing_groups':1,'groups_by_row_multiplicity':{2:1},'multiple_observation_groups':1,'cohorts':1,'weights_reconcile_per_cohort':True}
    approved={**actual,'groups_by_row_multiplicity':histogram}
    with pytest.raises(DiagnosticSpecError,match=code): compare_grouping_reproduction(approved,actual)

def test_histogram_normalizer_rejects_colliding_keys():
    with pytest.raises(DiagnosticSpecError,match='AMBIGUOUS_MULTIPLICITY_KEYS'):
        normalize_multiplicity_histogram({2:1,'2':1},source='fixture')

def test_execution_cli_passes_reloaded_audit_and_scores_synthetic_fixture(tmp_path):
    train={**_row('train','AAA','2023-01-01'),'label_up_5d':0,'return_5d':0}; valid={**_row('valid','BBB','2023-01-02'),'label_up_5d':1,'return_5d':.1}
    canonical=tmp_path/'canonical'; canonical.write_text(json.dumps(train)+'\n'+json.dumps(valid)+'\n')
    core={'train_canonical_observation_ids':['train','valid'],'test_canonical_observation_ids':['holdout']}; plan=tmp_path/'plan'; plan.write_text(json.dumps({**core,'plan_sha256':canonical_json_hash(core)}))
    manifest=tmp_path/'manifest'; manifest.write_text(json.dumps({'challengers':[{'model_version':'c1','candidate_lane':'ranking'}]}))
    capture_payload=[{'model_version':'c1','fold_index':1,'decision_threshold':.5,'score_semantics':'probability','target_definition':{'name':'label_up_5d'},'train_ids':['train'],
      'records':[{'id':'valid','score':.8,'label':1,'return':.1}]}]
    capture=tmp_path/'capture'; capture.write_text(json.dumps(capture_payload)); inputs={'canonical':canonical,'plan':plan,'manifest':manifest,'capture':capture}
    spec=tmp_path/'spec'; reviewed=_write_spec(spec,inputs,{'rows':1,'assignments':1})
    reproduced=grouping_audit([valid],[{'candidate':'c1','fold':1,'canonical_observation_id':'valid'}]); reproduced.update(status='AUDIT_COMPLETE_NO_SCORING',specification_sha256=reviewed,performance_scoring_executed=False,source_registration={'run':'fixture'})
    audit=tmp_path/'audit'; audit.write_text(json.dumps(reproduced)); audit.write_text(json.dumps(json.loads(audit.read_text())))
    out=tmp_path/'out'; command=[sys.executable,'-m','scripts.execute_alpha_atlas_v4_narrow_diagnostic','--specification',str(spec),'--reviewed-specification-hash',reviewed,
      '--canonical',str(canonical),'--plan',str(plan),'--manifest',str(manifest),'--capture',str(capture),'--approved-grouping-audit',str(audit),'--output-dir',str(out)]
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True)
    assert completed.returncode==0,completed.stderr
    result=json.loads((out/'narrow_diagnostic_results.json').read_text())
    assert result['execution_status']=='COMPLETE' and result['candidates'][0]['candidate']=='c1'
    assert result['grouping_reproduction_diagnostics']['representation_only'] is True
