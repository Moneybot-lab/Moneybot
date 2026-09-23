import json
import pytest
from pathlib import Path

from scripts.investigate_alpha_atlas_v4_ranking_zero_selection import investigate

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
