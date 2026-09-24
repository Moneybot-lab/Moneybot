from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import numpy as np
import pytest

from moneybot.services.alpha_atlas_v4_group_return_regression import (ContractError, TARGET, audit_group_timing, check_partitions,
  construct_groups, evaluate, fit_ridge, predict, select, timestamp_evidence)

FEATURES=["a","b","c"]
def row(identifier="x",ticker="AAA",value=1.0,outcome=.1,decision="2026-01-02T14:00:00+00:00",entry="2026-01-02T14:01:00+00:00",exit_at="2026-01-09T21:00:00+00:00"):
    return {"canonical_observation_id":identifier,"symbol":ticker,"event_date":"2026-01-02","label_horizon_sessions":5,
      "decision_at":decision,"feature_cutoff_at":decision,"entry_at":entry,"exit_at":exit_at,"feature_family_source_at":{"daily":"2026-01-01T21:00:00+00:00"},
      "a":value,"b":None,"c":7.0,"return_5d":outcome}

def test_group_aggregation_equal_observation_and_equal_group_weight():
    groups=construct_groups([row("a",value=1,outcome=.1),row("b",value=3,outcome=.3)],FEATURES,outcomes_allowed=True)
    assert groups[0]["features"]==[2.0,None,7.0]
    assert groups[0]["target"]==pytest.approx(.2) and groups[0]["training_weight"]==1.0
    assert groups[0]["feature_finite_counts"]==[2,0,2]

def test_hand_calculated_mean_loss_ridge_unpenalized_intercept():
    groups=[]
    for x,y in [(-1.,-1.),(1.,1.)]:
        g=construct_groups([row(str(x),value=x,outcome=y)],FEATURES,outcomes_allowed=True)[0]; groups.append(g)
    state,_=fit_ridge(groups,FEATURES)
    # z=[-1,1], X'X/n=1 and X'y/n=1, so beta=1/(1+alpha)=0.5.
    assert state["coefficients"][0]==pytest.approx(.5)
    assert state["intercept"]==pytest.approx(0) and state["intercept_penalized"] is False
    assert state["all_missing_training_features"]==["b"] and state["zero_variance_training_features"]==["b","c"]

def test_registered_svd_fallback(monkeypatch):
    groups=[construct_groups([row("a",value=-1,outcome=-1)],FEATURES,outcomes_allowed=True)[0],construct_groups([row("b",value=1,outcome=1)],FEATURES,outcomes_allowed=True)[0]]
    monkeypatch.setattr(np.linalg,"solve",lambda *_: (_ for _ in ()).throw(np.linalg.LinAlgError()))
    state,_=fit_ridge(groups,FEATURES)
    assert state["solver"]=="svd_pseudoinverse_rcond_1e-12"

def test_training_only_preprocessing_and_validation_outcome_independence():
    train=[construct_groups([row("t",value=2,outcome=.2)],FEATURES,outcomes_allowed=True)[0]]
    state,_=fit_ridge(train,FEATURES)
    v1=construct_groups([row("v",ticker="ZZZ",value=100,outcome=.1,decision="2026-02-02T14:00:00+00:00",entry="2026-02-02T14:01:00+00:00",exit_at="2026-02-09T21:00:00+00:00")],FEATURES,outcomes_allowed=False)
    changed=row("v",ticker="ZZZ",value=100,outcome=999,decision="2026-02-02T14:00:00+00:00",entry="2026-02-02T14:01:00+00:00",exit_at="2026-02-09T21:00:00+00:00")
    v2=construct_groups([changed],FEATURES,outcomes_allowed=False)
    assert state["means"][0]==2 and predict(v1,state)==predict(v2,state)
    assert select(predict(v1,state),1)==select(predict(v2,state),1)

def test_malformed_groups_duplicate_assignments_and_temporal_crossings_fail():
    with pytest.raises(ContractError,match="INCOMPATIBLE_GROUP_TIMING"):
        construct_groups([row("a"),row("b",decision="2026-01-02T13:00:00+00:00")],FEATURES,outcomes_allowed=True)
    with pytest.raises(ContractError,match="MISSING_OR_DUPLICATE_CANONICAL_ID"):
        construct_groups([row("a"),row("a")],FEATURES,outcomes_allowed=True)
    train=construct_groups([row("a")],FEATURES,outcomes_allowed=True)
    validation=construct_groups([row("b")],FEATURES,outcomes_allowed=False)
    with pytest.raises(ContractError,match="CROSS_PARTITION_GROUP"): check_partitions(train,validation)
    late=construct_groups([row("c",ticker="CCC",decision="2026-01-03T14:00:00+00:00",entry="2026-01-03T14:01:00+00:00",exit_at="2026-01-10T21:00:00+00:00")],FEATURES,outcomes_allowed=True)
    early=construct_groups([row("d",ticker="DDD",decision="2026-01-04T14:00:00+00:00",entry="2026-01-04T14:01:00+00:00",exit_at="2026-01-11T21:00:00+00:00")],FEATURES,outcomes_allowed=False)
    with pytest.raises(ContractError,match="PURGE_OR_EMBARGO_VIOLATION"): check_partitions(late,early)

def test_equivalent_timezone_representations_are_same_instant_but_real_difference_blocks():
    first=row("a"); second=row("b")
    second.update(decision_at="2026-01-02T09:00:00-05:00",feature_cutoff_at="2026-01-02T09:00:00-05:00",
                  entry_at="2026-01-02T09:01:00-05:00",exit_at="2026-01-09T16:00:00-05:00")
    groups=construct_groups([first,second],FEATURES,outcomes_allowed=True)
    assert len(groups)==1 and groups[0]["decision_at"]=="2026-01-02T14:00:00Z"
    second["decision_at"]="2026-01-02T09:00:01-05:00"; second["feature_cutoff_at"]=second["decision_at"]
    with pytest.raises(ContractError,match="INCOMPATIBLE_GROUP_TIMING") as caught: construct_groups([first,second],FEATURES,outcomes_allowed=True)
    assert caught.value.details["timestamp_values"][1]["normalized_utc"]=="2026-01-02T14:00:01Z"

@pytest.mark.parametrize("value,status",[(None,"INVALID_REQUIRED_TIMESTAMP"),("2026-01-02T14:00:00","TIMEZONE_AMBIGUOUS_TIMESTAMP")])
def test_missing_and_timezone_ambiguous_timestamps_fail_closed(value,status):
    evidence=timestamp_evidence(value); assert evidence["status"]==status and evidence["normalized_utc"] is None
    item=row(); item["decision_at"]=value
    with pytest.raises(ContractError): construct_groups([item],FEATURES,outcomes_allowed=True)

def test_temporal_order_violation_is_not_normalized_away():
    item=row(decision="2026-01-02T15:00:00+00:00",entry="2026-01-02T14:01:00+00:00")
    item["feature_cutoff_at"]="2026-01-02T14:00:00+00:00"
    with pytest.raises(ContractError,match="TEMPORAL_ORDER_VIOLATION"): construct_groups([item],FEATURES,outcomes_allowed=True)

def test_complete_timing_audit_collects_all_issues_without_outcomes():
    okay=row("a"); bad=row("b"); bad["decision_at"]="2026-01-02T14:00:01+00:00"; bad["feature_cutoff_at"]=bad["decision_at"]
    report=audit_group_timing([(1,"train",[okay,bad]),(1,"validation",[okay])])
    assert report["affected_group_assignments"]==1 and report["outcomes_read"] is False
    assert report["partitions"][0]["affected_unique_rows"]==2 and report["partitions"][1]["affected_unique_rows"]==0
    assert report["issues"][0]["fold"]==1 and report["issues"][0]["partition"]=="train"

def test_ties_and_input_order_are_deterministic_and_missing_outcomes_do_not_reselect():
    base={"event_date":"2026-01-02","label_horizon_sessions":5,"entry_at":"e","exit_at":"x","prediction":.1,"member_count":1}
    rows=[dict(base,stable_group_identity=t,source_canonical_observation_ids=[t],ticker=t,decision_at="d",feature_cutoff_at="d") for t in "GFEDCBA"]
    first,_=select(rows,1); second,_=select(list(reversed(rows)),1)
    assert [(x["ticker"],x["selected"]) for x in first]==[(x["ticker"],x["selected"]) for x in second]
    assert [x["ticker"] for x in first if x["selected"]]==list("ABCDE")
    snapshot=json.loads(json.dumps(first)); result=evaluate(first,[],1)
    assert result["status"]=="BLOCKED" and first==snapshot

def test_effective_target_metadata_and_unauthorized_alpha():
    group=construct_groups([row()],FEATURES,outcomes_allowed=True); state,_=fit_ridge(group,FEATURES)
    record=predict(construct_groups([row("v")],FEATURES,outcomes_allowed=False),state)[0]
    assert record["effective_training_target_name"]==TARGET and record["prediction_semantics"]=="predicted_return"
    assert "label_up_5d" not in json.dumps(record) and "daily_top5" not in json.dumps(record)
    with pytest.raises(ContractError,match="UNAUTHORIZED_CONFIGURATION"): fit_ridge(group,FEATURES,alpha=2)

def test_exact_cli_module_entry_point():
    result=subprocess.run([sys.executable,"-m","scripts.run_alpha_atlas_v4_group_return_regression","--help"],capture_output=True,text=True)
    assert result.returncode==0 and "--registration" in result.stdout and "--output-dir" in result.stdout

def test_registration_and_authorization_are_hash_bound():
    registration=Path("docs/reports/alpha_atlas_v4_group_return_regression_registration.v1.json")
    authorization=json.loads(Path("docs/reports/alpha_atlas_v4_group_return_regression_execution_authorization.v1.json").read_text())
    import hashlib
    assert hashlib.sha256(registration.read_bytes()).hexdigest()==authorization["registration_sha256"]
    assert authorization["authorized_scope"]["development_fold_fits"]==3 and authorization["authorized_scope"]["final_holdout_fits"]==0
