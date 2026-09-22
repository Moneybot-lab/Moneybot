from __future__ import annotations
import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest
from moneybot.services.alpha_atlas_v4_narrow_diagnostic import DiagnosticSpecError, descriptive_metrics, equal_fold_aggregate, grouping_audit, validate_spec
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash


def _row(identifier,ticker,date,horizon=5,entry="2023-01-03T14:30:00+00:00",exit_at="2023-01-10T21:00:00+00:00"):
    return {"canonical_observation_id":identifier,"symbol":ticker,"event_date":date,"label_horizon_sessions":horizon,"entry_at":entry,"exit_at":exit_at}

def _write_spec(path,inputs):
    frozen={role:{"file_byte_sha256":hashlib.sha256(source.read_bytes()).hexdigest()} for role,source in inputs.items()}
    plan=json.loads(inputs['plan'].read_text()); frozen['plan'].update(semantic_content_sha256=plan['plan_sha256'],semantic_content_hash_algorithm='canonical_json_hash_without_plan_sha256')
    path.write_text(json.dumps({"frozen_inputs":frozen,"execution":{"default":"OFF"},"broader_registration":{"status":"REGISTERED_BLOCKED"},"source_registration":{"run":"fixture"}}))
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
