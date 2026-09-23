import json
import pytest
from pathlib import Path
import subprocess
import sys

from scripts.investigate_alpha_atlas_v4_ranking_zero_selection import ProvenanceResolutionError, investigate, resolve_code_reference

NAME="challenger-ranking-lane-full-v1"

def item(predictions):
    return {"model_version":NAME,"model_type":"ranking_lane_linear","fold_index":1,"decision_threshold":.5,"score_semantics":"ranking_score_not_buy_probability","predictions":predictions,"records":[{"id":"a","score":.4,"label":1,"return":.1,"abstained":False,"risk_rejected":False,"rule_rejected":False},{"id":"b","score":.6,"label":0,"return":-.1,"abstained":False,"risk_rejected":True,"rule_rejected":False}]}

def test_investigation_detects_threshold_misinterpretation_and_reconciles():
    result=investigate([item([1,1])],{"challengers":[{"model_version":NAME,"candidate_lane":"ranking","spec":{"target_policy":"daily_top5"}}]})
    fold=result["candidate_folds"][0]
    assert fold["classification"]=="DIAGNOSTIC_INTERPRETATION_DEFECT"
    assert fold["source_recorded_selected"]==2 and fold["diagnostic_threshold_selected"]==0
    assert fold["final_dispositions"]=={"RISK_REJECTED":1,"SELECTED":1} and fold["reconciles"]

def test_missing_is_not_false_and_false_is_valid():
    missing=item(None)
    result=investigate([missing],{"challengers":[{"model_version":NAME}]})
    assert result["candidate_folds"][0]["classification"]=="SAVED_SELECTION_EVIDENCE_INSUFFICIENT"
    valid=investigate([item([0,False])],{"challengers":[{"model_version":NAME}]})["candidate_folds"][0]
    assert valid["source_recorded_selected"]==0

@pytest.mark.parametrize("bad",[[1],[1,None],[1,.0]])
def test_selection_evidence_rejects_length_null_and_fractional_types(bad):
    result=investigate([item(bad)],{"challengers":[{"model_version":NAME}]})
    assert result["candidate_folds"][0]["classification"]=="SAVED_SELECTION_EVIDENCE_INSUFFICIENT"

def test_manual_investigation_workflow_is_pinned_and_never_scores():
    text=Path('.github/workflows/v4-investigate-ranking-zero-selection.yml').read_text()
    assert text.startswith('name: V4 Investigate Ranking Zero Selection\n')
    assert 'workflow_dispatch:' in text and 'push:' not in text and 'schedule:' not in text
    assert '35795768048' in text and '10723897652' in text and '35788348286' in text
    assert 'scripts.investigate_alpha_atlas_v4_ranking_zero_selection' in text
    assert 'scripts.execute_alpha_atlas_v4_narrow_diagnostic' not in text
    assert 'git fetch --no-tags --depth=1 origin 1f8f46db584dff0881273bdeae1c56c1a8a016c5 5d360cdbda802ae8527b35fe59f75920b8c827c8' in text
    assert 'if ! test -s "$OUTPUT_DIR/reports/ranking_zero_selection_investigation.json"' in text

def test_historical_source_paths_resolve_to_expected_blobs_and_bytes():
    entry=resolve_code_reference('entry','5d360cdbda802ae8527b35fe59f75920b8c827c8','scripts/capture_alpha_atlas_v4_development_oof.py','test')
    producer=resolve_code_reference('producer','5d360cdbda802ae8527b35fe59f75920b8c827c8','scripts/train_challenger_suite.py','test')
    assert entry['git_blob_object_id']=='fec183ca88967e5f18eed3cae3d6afde5f573d78'
    assert entry['file_byte_sha256']=='5a778d7a04e799ecc8078fc22ee5122f05af4c2312d45f7dcaa934fb88409df9'
    assert producer['git_blob_object_id']=='35eaaf821f910b14c76116dae127650ac48e6bf1'
    assert producer['file_byte_sha256']=='13fe60562efcdd93b4642c01455cff67e07d4ae1520d8443f74cdcf9ecd73951'

def test_missing_historical_path_is_specific():
    with pytest.raises(ProvenanceResolutionError) as caught:
        resolve_code_reference('producer','5d360cdbda802ae8527b35fe59f75920b8c827c8','scripts/not-there.py','test')
    assert caught.value.details['role']=='producer' and caught.value.details['failed_operation']

def test_cli_preserves_specific_provenance_failure_report(tmp_path):
    capture=tmp_path/'capture.json'; capture.write_text('[]')
    manifest=tmp_path/'manifest.json'; manifest.write_text('{"challengers":[]}')
    completed=tmp_path/'completed.json'; completed.write_text(json.dumps({'execution_status':'COMPLETE','specification_sha256':'206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2'}))
    audit=tmp_path/'audit.json'; audit.write_text(json.dumps({'status':'AUDIT_COMPLETE_NO_SCORING','specification_sha256':'206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2'}))
    workflow=tmp_path/'workflow.json'; workflow.write_text(json.dumps({'diagnostic_code_sha':'5d360cdbda802ae8527b35fe59f75920b8c827c8','capture_reused':True}))
    provenance=tmp_path/'provenance.json'; provenance.write_text(json.dumps({'mode':'development_only_frozen_fold_refit'})); out=tmp_path/'out'
    command=[sys.executable,'-m','scripts.investigate_alpha_atlas_v4_ranking_zero_selection','--capture',str(capture),'--manifest',str(manifest),'--completed-results',str(completed),'--approved-audit',str(audit),'--workflow-provenance',str(workflow),'--capture-provenance',str(provenance),'--output-dir',str(out)]
    result=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert result.returncode==2
    report=json.loads((out/'ranking_zero_selection_investigation.json').read_text())
    assert report['reason_code']=='INVESTIGATION_VALIDATION_FAILED'
    assert 'capture producer commit is not established' in report['readable_reason']
    assert (out/'ranking_zero_selection_investigation.md').is_file() and (out/'provenance_manifest.json').is_file() and (out/'SHA256SUMS').is_file()
