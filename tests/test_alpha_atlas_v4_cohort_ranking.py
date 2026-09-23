from __future__ import annotations
import hashlib, json, subprocess, sys
from pathlib import Path
import pytest

from moneybot.services.alpha_atlas_v4_cohort_ranking import (
    EVIDENCE_SHA256, PROPOSAL_SHA256, RankingContractError, aggregate_folds,
    score_candidate_fold, validate_frozen_contract,
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

@pytest.mark.parametrize("change,code",[("missing","MISSING_REQUIRED_EVIDENCE"),("null_gate","MISSING_REQUIRED_EVIDENCE"),("string_gate","MALFORMED_GATE"),("nan","MISSING_OR_NONFINITE_FIELD")])
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
    return proposal,evidence,lineage

def test_exact_proposal_evidence_hashes_and_lineage_validate(tmp_path):
    proposal,evidence,lineage=_copy_pins(tmp_path)
    assert hashlib.sha256(proposal.read_bytes()).hexdigest()==PROPOSAL_SHA256
    assert validate_frozen_contract(proposal,evidence,lineage)["lineage"]["diagnostic_run"]=="35795768048-1"

@pytest.mark.parametrize("which,code",[("proposal","FROZEN_HASH_MISMATCH"),("evidence","FROZEN_HASH_MISMATCH"),("lineage","AUDIT_LINEAGE_MISMATCH")])
def test_mismatched_hashes_and_audit_lineage_fail_closed(tmp_path,which,code):
    proposal,evidence,lineage=_copy_pins(tmp_path)
    if which=="lineage": lineage.write_text("{}")
    else: {"proposal":proposal,"evidence":evidence}[which].write_text("{}")
    with pytest.raises(RankingContractError,match=code): validate_frozen_contract(proposal,evidence,lineage)

def test_malformed_json_and_cli_real_scoring_gate_emit_audit(tmp_path):
    proposal,evidence,lineage=_copy_pins(tmp_path); lineage.write_text("{")
    with pytest.raises(RankingContractError,match="MALFORMED_OR_MISSING_JSON"): validate_frozen_contract(proposal,evidence,lineage)
    _,_,lineage=_copy_pins(tmp_path); output=tmp_path/"audit.json"
    command=[sys.executable,"-m","scripts.validate_alpha_atlas_v4_cohort_ranking","--proposal",str(proposal),"--evidence",str(evidence),"--lineage",str(lineage),"--output",str(output),"--request-real-scoring"]
    completed=subprocess.run(command,text=True,capture_output=True)
    report=json.loads(output.read_text())
    assert completed.returncode==2 and report["reason_code"]=="REAL_SCORING_HARD_DISABLED_REQUIRES_CODE_CHANGE"
    assert report["scoring"]=="NOT_RUN" and report["real_scoring_enabled"] is False
