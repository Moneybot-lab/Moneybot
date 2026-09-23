from __future__ import annotations
import copy, json, os, subprocess, sys
from pathlib import Path
import pytest
from moneybot.services import alpha_atlas_v4_baseline_registration as reg

def _write(tmp_path: Path, *, symbol="AAPL", omit_typed=False):
    rows=[]
    for i in range(7):
        day=f"2023-03-{i+1:02d}"; row={"canonical_observation_id":f"id{i}","symbol":symbol if i==3 else "AAPL",
          "event_date":day,"feature_cutoff_at":f"{day}T20:00:00+00:00","decision_at":f"{day}T20:01:00+00:00",
          "entry_at":f"{day}T20:02:00+00:00","label_start_at":f"{day}T20:02:00+00:00","exit_at":f"{day}T20:03:00+00:00",
          "entry_session_date":day,"exit_session_date":day,"entry_price":10,"exit_price":11,"valuation_certification":{"status":"VERIFIED"}}
        if not omit_typed: row.update(point_in_time_symbol_id=f"pid-{i}",share_class_figi=f"share-{i}",composite_figi=f"composite-{i}",cik=f"cik-{i}")
        rows.append(row)
    canonical=tmp_path/'all.jsonl'; canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    folds=[]
    for i in range(1,4):
        folds.append({"fold_index":i,"usable":True,"train_ids":[f"id{i-1}"],"validation_ids":[f"id{i}"],
          "embargo_sessions":1,"embargo_session_dates":[],"complete_group_integrity":True,"invalid_session_group_count":0,"timing_boundary_passed":True})
    plan={"plan_sha256":"plan-hash","train_canonical_observation_ids":[f"id{i}" for i in range(6)],"test_canonical_observation_ids":["id6"]}
    plan_path=tmp_path/'plan.json'; plan_path.write_text(json.dumps(plan))
    manifest={"challengers":[{"model_version":"candidate","candidate_lane":"decision","spec":{"threshold":.6,"sample_weight_policy":"uniform"}}],"walk_forward_windows":folds}
    manifest_path=tmp_path/'manifest.json'; manifest_path.write_text(json.dumps(manifest))
    capture=[]
    for fold in folds:
        vid=fold['validation_ids'][0]
        capture.append({"model_version":"candidate","fold_index":fold['fold_index'],"train_ids":fold['train_ids'],"validation_ids":[vid],"decision_threshold":.6,"score_semantics":"probability","target_definition":{"name":"label_up_5d","horizon_sessions":5},"records":[{"id":vid,"score":.7,"label":1,"security":"AAPL","session":f"2023-03-{fold['fold_index']+1:02d}"}]})
    capture_path=tmp_path/'capture.json'; capture_path.write_text(json.dumps(capture))
    expected={"canonical":reg.sha(canonical),"plan":"plan-hash","manifest":reg.sha(manifest_path),"capture":reg.sha(capture_path)}
    source={x:{"source_run":1,"source_attempt":1,"source_head_sha":"a"*40,"artifact_id":1,"artifact_name":"fixture","artifact_digest":"sha256:"+"b"*64} for x in expected}
    source['materiality_evidence']={"unresolved_identity_typed_identifier_map_status":"VERIFIED_COMPLETE_NO_MATCH"}
    return canonical,plan_path,manifest_path,capture_path,source,expected

def _run(monkeypatch,tmp_path,**kwargs):
    paths=kwargs.pop('paths',None) or _write(tmp_path,**kwargs); *files,source,expected=paths
    monkeypatch.setattr(reg,"EXPECTED",expected)
    return reg.register(*files,source),paths

def test_complete_registration_binds_inputs_without_performance(monkeypatch,tmp_path):
    result,paths=_run(monkeypatch,tmp_path)
    assert result['status']=='REGISTERED_READY_FOR_REVIEW'
    assert result['development_scope']['fold_count']==3
    assert result['split_integrity']['final_holdout_overlap_count']==0
    assert result['split_integrity']['final_holdout_content_loaded'] is False
    assert result['materiality']['unique_affected_rows']==0
    assert result['performance_comparison_executed'] is False
    assert result['metrics']['drawdown'].startswith('NOT_EVALUABLE')
    assert all(item['bytes'] and item['sha256_computed'] for item in result['inputs'])

def test_altered_hash_fails(monkeypatch,tmp_path):
    paths=_write(tmp_path); *files,source,expected=paths; expected['capture']='0'*64; monkeypatch.setattr(reg,'EXPECTED',expected)
    with pytest.raises(reg.RegistrationError,match='INPUT_HASH_MISMATCH'): reg.register(*files,source)

@pytest.mark.parametrize(('mutation','reason'),[("duplicate","DUPLICATE_OOF_RECORD"),("missing","MISSING_OR_UNEXPECTED_OOF_RECORD"),("wrong_fold","OOF_FOLD_MEMBERSHIP_MISMATCH"),("holdout","FINAL_HOLDOUT_OVERLAP")])
def test_oof_integrity_failures_are_reported(monkeypatch,tmp_path,mutation,reason):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths; data=json.loads(capture.read_text())
    if mutation=='duplicate': data[0]['records'].append(copy.deepcopy(data[0]['records'][0]))
    elif mutation=='missing': data[0]['records']=[]
    elif mutation=='wrong_fold': data[0]['train_ids']=['id5']
    else: data[0]['validation_ids']=['id6']; data[0]['records']=[{"id":"id6","score":.7,"label":1}]
    capture.write_text(json.dumps(data)); expected['capture']=reg.sha(capture); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    assert result['status']=='REGISTERED_BLOCKED'; assert reason in result['split_integrity']['reason_codes']

def test_purge_violation_blocks(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]; rows[0]['exit_at']='2023-04-01T00:00:00+00:00'; canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    assert 'PURGE_OR_LABEL_WINDOW_VIOLATION' in result['split_integrity']['reason_codes']

def test_affected_dependency_window_blocks(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]; rows[1].update(symbol='KAII',feature_cutoff_at='2023-02-16T20:00:00+00:00',exit_at='2023-02-25T20:00:00+00:00'); canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    assert result['materiality']['status']=='BLOCKED'; assert set(result['materiality']['affected_rows'][0]['dependency_dates'])=={'2023-02-17','2023-02-24'}

def test_unavailable_typed_identity_is_unknown_not_zero(monkeypatch,tmp_path):
    paths=_write(tmp_path,omit_typed=True); paths[-2].pop('materiality_evidence')
    result,_=_run(monkeypatch,tmp_path,paths=paths)
    assert result['materiality']['status']=='UNKNOWN'
    assert result['materiality']['typed_identifier_mapping_status']=='PARTIAL_UNKNOWN'
    assert result['split_integrity']['status']=='VERIFIED'
    assert 'TYPED_IDENTITY_MATERIALITY_UNKNOWN' in result['readiness_reason_codes']

def test_exact_module_entrypoint_generates_registration_only(tmp_path):
    canonical,plan,manifest,capture,source,expected=_write(tmp_path)
    provenance=tmp_path/'source.json'; provenance.write_text(json.dumps(source)); output=tmp_path/'out'
    command=[sys.executable,'-m','scripts.register_alpha_atlas_v4_baseline_comparison',
      '--canonical',str(canonical),'--plan',str(plan),'--manifest',str(manifest),'--capture',str(capture),
      '--source-provenance',str(provenance),'--output-dir',str(output)]
    for role,value in expected.items(): command += [f'--expected-{role}-hash',value]
    env=os.environ.copy(); env.pop('PYTHONPATH',None)
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
    assert completed.returncode==0,completed.stderr
    payload=json.loads((output/'registration.json').read_text())
    assert payload['performance_comparison_executed'] is False
    assert payload['registration_sha256']
    assert {p.name for p in output.iterdir()} == {'registration.json','registration.md','split_integrity.json','materiality.json','materiality.md','input_manifest.json','identity_dependencies.json','identity_dependencies.md','comparison_scope_decision.json','comparison_scope_decision.md','SHA256SUMS'}

def test_manual_workflow_is_registration_only_and_pinned():
    text=Path('.github/workflows/v4-register-development-baseline-comparison.yml').read_text()
    assert text.startswith('name: V4 Register Development Baseline Comparison\n')
    assert 'workflow_dispatch:' in text and 'push:' not in text and 'schedule:' not in text
    assert 'actions: read' in text and 'contents: read' in text
    assert '34689216730' in text and '34769717178' in text
    assert 'python -m scripts.register_alpha_atlas_v4_baseline_comparison' in text
    assert '--metadata-only' in text and 'if: always()' in text
    assert 'train_challenger_suite' not in text and 'MASSIVE_API_KEY' not in text

def test_nested_execution_prices_fix_false_all_row_missing(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows:
        row.pop('entry_price'); row.pop('exit_price'); row['reconstruction_lineage']={'execution':{'entry_price':10,'exit_price':11,'entry_at':row['entry_at'],'exit_at':row['exit_at']}}
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    assert result['materiality']['price_evidence']['mutually_exclusive_counts']=={'both_present':6}

def test_returns_present_without_prices_does_not_claim_price_evidence(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows: row.pop('entry_price'); row.pop('exit_price'); row['return_5d']=.01
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source); materiality=result['materiality']
    assert materiality['price_evidence']['mutually_exclusive_counts']=={'both_missing':6}
    assert materiality['return_evidence']['frozen_endpoint_return_present']==6
    assert materiality['return_evidence']['return_semantics'].startswith('frozen endpoint return')

def test_missing_costs_differ_from_explicit_zero_and_already_net(monkeypatch,tmp_path):
    for mode in ('missing','zero','already_net'):
        root=tmp_path/mode; root.mkdir(); paths=_write(root); canonical,plan,manifest,capture,source,expected=paths
        rows=[json.loads(x) for x in canonical.read_text().splitlines()]
        for row in rows:
            row['return_5d']=.01
            if mode=='zero': row['reconstruction_lineage']={'execution':{'transaction_cost_bps':0,'entry_slippage_bps':0,'exit_slippage_bps':0}}
            if mode=='already_net': row['return_is_net']=True
        canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
        result=reg.register(canonical,plan,manifest,capture,source); eligibility=result['metric_eligibility']['net_endpoint_economics']
        if mode=='missing': assert eligibility['status']=='NOT_EVALUABLE' and 'APPLICABLE_NUMERIC_COST_POLICY_UNAVAILABLE' in eligibility['reason_codes']
        elif mode=='zero': assert eligibility['status']=='EVALUABLE' and eligibility['cost_application']=='ZERO_ONLY_IF_EXPLICIT'
        else: assert eligibility['status']=='EVALUABLE' and eligibility['cost_application']=='NO_ADDITIONAL_SUBTRACTION'

def test_partial_identifier_coverage_and_summary_do_not_imply_zero_overall(monkeypatch,tmp_path):
    paths=_write(tmp_path,omit_typed=True); canonical,plan,manifest,capture,source,expected=paths; source.pop('materiality_evidence'); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    assert result['materiality']['direct_historical_match_rows']==0
    assert result['materiality']['status']=='UNKNOWN'
    assert result['materiality']['identifier_coverage']['share_class_figi']['development_rows_missing']==6
    assert result['status']=='REGISTERED_BLOCKED'

def test_typed_mapping_match_without_dated_applicability_is_subset_unknown(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths; monkeypatch.setattr(reg,'EXPECTED',expected)
    identity=tmp_path/'identity.json'; identity.write_text(json.dumps({'records':[{'ticker':'BWINA','share_class_figi':'share-2'}]}))
    result=reg.register(canonical,plan,manifest,capture,source,identity_evidence=identity)
    assert result['materiality']['typed_identifier_mapping_status']=='UNKNOWN_AMBIGUOUS_TYPED_MATCHES'
    assert [row['canonical_id'] for row in result['materiality']['ambiguous_typed_matches']]==['id2']
    assert result['materiality']['unique_affected_rows']==0

def test_prior_registration_is_preserved_and_correction_gets_new_hash(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths; monkeypatch.setattr(reg,'EXPECTED',expected)
    old_hash='019fb1387941858c4588f19bb89b8acd34144487025f0233d6e20cb4bfafb107'
    prior=tmp_path/'registration.json'; prior.write_text(json.dumps({'registration_sha256':old_hash,'status':'REGISTERED_BLOCKED'}))
    result=reg.register(canonical,plan,manifest,capture,source,prior_registration=prior)
    assert result['prior_registration']['internal_hash_matches_expected'] is True
    assert result['prior_registration']['internal_registration_sha256']==old_hash
    assert result['registration_sha256']!=old_hash
    assert result['correction_change_record']

def test_other_scope_numeric_policy_is_recorded_but_not_borrowed(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths; monkeypatch.setattr(reg,'EXPECTED',expected)
    policy=tmp_path/'execution_policy.json'; policy.write_text(json.dumps({'policy_version':'portfolio-v1','transaction_cost_bps':5,'slippage_bps':5}))
    result=reg.register(canonical,plan,manifest,capture,source,cost_policy_evidence=policy)
    evidence=result['materiality']['cost_evidence']['other_saved_policy_evidence']
    assert evidence['status']=='LOCATED_DIFFERENT_SCOPE' and evidence['applicable_to_development_oof'] is False
    assert result['metric_eligibility']['net_endpoint_economics']['status']=='NOT_EVALUABLE'

def test_complete_returns_without_bound_producer_trace_are_not_labeled_gross(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows: row['return_5d']=.1
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    gross=result['metric_eligibility']['gross_endpoint_economics']
    assert gross['status']=='NOT_EVALUABLE'
    assert gross['reason_codes']==['GROSS_RETURN_SEMANTICS_UNVERIFIED']

def test_bound_producer_trace_and_price_formula_verify_gross_semantics(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows:
        row['return_5d']=.1
        row['label_split_adjustment_factor']=1.0
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    source['canonical']['source_head_sha']='1f8f46db584dff0881273bdeae1c56c1a8a016c5'
    source['capture']['source_head_sha']='5d360cdbda802ae8527b35fe59f75920b8c827c8'
    result=reg.register(canonical,plan,manifest,capture,source)
    evidence=result['materiality']['return_evidence']
    assert evidence['economic_semantics_status']=='VERIFIED_GROSS_SPLIT_ADJUSTED_PRICE_RETURN'
    assert evidence['formula_consistency']['counts']=={'matched':3}
    assert result['metric_eligibility']['gross_endpoint_economics']['status']=='EVALUABLE'
    assert result['metric_eligibility']['net_endpoint_economics']['status']=='NOT_EVALUABLE'

def test_point_in_time_symbol_id_name_does_not_overstate_security_identity(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows: row['point_in_time_symbol_id']=f"{row['symbol']}:{row['event_date']}"
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    result=reg.register(canonical,plan,manifest,capture,source)
    lineage=result['materiality']['point_in_time_symbol_lineage']
    assert lineage['fallback_pattern_rows']==6
    assert lineage['semantic_guarantee']=='TICKER_DATE_OBSERVATION_KEY_NOT_PROVEN_SECURITY_OR_LISTING_ID'
    assert lineage['identity_sufficiency_for_frozen_comparison'].startswith('UNKNOWN_')

def test_identity_dependency_windows_include_features_labels_and_execution(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    for row in rows:
        row['point_in_time_symbol_id']=f"{row['symbol']}:{row['event_date']}"
        row['feature_family_source_at']={'symbol_daily':'2023-02-15T21:00:00+00:00','fundamental':'2023-01-31T00:00:00+00:00'}
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    report=reg.register(canonical,plan,manifest,capture,source)['materiality']['identity_dependency_report']
    assert report['development_rows_examined']==6 and report['validation_rows_examined']==3
    assert report['row_classification_counts']=={'UNRESOLVED_IDENTITY':6}
    assert report['validation_affected_rows']==3
    assert report['dependency_occurrence_counts']['feature_history']==6
    assert report['overall_dependency_window']['earliest_saved_feature_source']=='2023-01-31'
    assert report['overall_dependency_window']['full_feature_lookback_start'].startswith('UNKNOWN_')
    decision=reg.register(canonical,plan,manifest,capture,source)['comparison_scope_decision']
    assert decision['status']=='PROPOSAL_REQUIRES_REVIEW'
    assert decision['recommended_option']=='A_NARROW_FROZEN_SAMPLE_DIAGNOSTIC_ONLY'
    assert decision['option_a']['comparison_rule'].startswith('UNCHANGED_REGISTERED_RULE')
    assert decision['option_b']['new_assumption_not_recovered_evidence'] is True

def test_cross_symbol_point_id_collision_is_conflicting_not_supported(monkeypatch,tmp_path):
    paths=_write(tmp_path); canonical,plan,manifest,capture,source,expected=paths
    rows=[json.loads(x) for x in canonical.read_text().splitlines()]
    rows[0]['point_in_time_symbol_id']='shared'; rows[1]['point_in_time_symbol_id']='shared'; rows[1]['symbol']='MSFT'
    canonical.write_text(''.join(json.dumps(x)+'\n' for x in rows)); expected['canonical']=reg.sha(canonical); monkeypatch.setattr(reg,'EXPECTED',expected)
    report=reg.register(canonical,plan,manifest,capture,source)['materiality']['identity_dependency_report']
    assert report['conflicting_rows']==2
    assert {x['canonical_id'] for x in report['affected_rows'] if x['classification']=='CONFLICTING'}=={'id0','id1'}
