#!/usr/bin/env python3
"""Run the hash-bound Alpha Atlas V4 group-return regression exactly once."""
from __future__ import annotations
import argparse, json, os, resource, shutil, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows
from moneybot.services.alpha_atlas_v4_group_return_regression import (ContractError, TARGET, audit_group_timing, check_partitions, construct_groups,
  evaluate, file_hash, fit_ridge, predict, select, validate_bindings)

VALIDATION_ALLOWLIST={"canonical_observation_id","symbol","ticker","event_date","label_horizon_sessions","decision_at","feature_cutoff_at","entry_at","exit_at","feature_family_source_at"}

def write(path:Path,value)->None: path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")

def execute(a)->dict:
    started=time.monotonic(); registration=json.loads(a.registration.read_text()); features=registration["features"]["list"]
    provenance=validate_bindings(a.registration,a.authorization,{"canonical":a.canonical,"plan":a.plan,"manifest":a.manifest})
    plan=json.loads(a.plan.read_text()); development=set(map(str,plan["train_canonical_observation_ids"])); holdout=set(map(str,plan.get("test_canonical_observation_ids",[])))
    manifest=json.loads(a.manifest.read_text()); folds=[x for x in manifest["walk_forward_windows"] if x.get("usable")]
    if len(folds)!=3: raise ContractError("EXACTLY_THREE_FOLDS_REQUIRED",{"observed":len(folds)})
    fold_members=[]
    for item in folds:
        fold=int(item["fold_index"]); train=list(map(str,item.get("train_canonical_observation_ids",item.get("train_ids",[])))); valid=list(map(str,item.get("validation_canonical_observation_ids",item.get("validation_ids",[]))))
        if len(train)!=len(set(train)) or len(valid)!=len(set(valid)) or set(train)&set(valid): raise ContractError("DUPLICATE_OR_CROSS_PARTITION_ASSIGNMENT",{"fold":fold})
        if (set(train)|set(valid))-development or (set(train)|set(valid))&holdout: raise ContractError("INVALID_DEVELOPMENT_MEMBERSHIP",{"fold":fold})
        fold_members.append((fold,train,valid))
    needed=set().union(*(set(t)|set(v) for _,t,v in fold_members)); rows,load_counts=_load_development_rows(a.canonical,needed)
    timing=audit_group_timing([(fold,partition,[rows[x] for x in ids]) for fold,train,valid in sorted(fold_members)
                               for partition,ids in (("train",train),("validation",valid))])
    write(a.output_dir/"timing_compatibility_audit.json",timing)
    if timing["affected_group_assignments"]:
        first=timing["issues"][0]
        raise ContractError("INCOMPATIBLE_GROUP_TIMING",{"summary":"genuinely different or invalid required timestamps within registered operational group",
          "affected_group_assignments":timing["affected_group_assignments"],"partitions":timing["partitions"],"first_issue":first,
          "completed_validation_stages":["registration_hash","authorization","frozen_input_byte_hashes","split_semantic_hashes","development_membership","complete_timing_metadata_scan"],
          "fits_started":0,"fits_completed":0,"predictions_written":False,"evaluation_executed":False,"holdout_content_access":False})
    audits=[]; mappings=[]; preprocessing=[]; models=[]; all_predictions=[]; all_selections=[]; all_cohorts=[]
    for fold,train_ids,valid_ids in sorted(fold_members):
        train_groups=construct_groups((rows[x] for x in train_ids),features,outcomes_allowed=True)
        # The prediction stage receives an explicit allowlist and never validation outcomes.
        safe=[]
        for identifier in valid_ids:
            row=rows[identifier]; safe.append({k:row[k] for k in VALIDATION_ALLOWLIST|set(features) if k in row})
        validation_groups=construct_groups(safe,features,outcomes_allowed=False); check_partitions(train_groups,validation_groups)
        if len(train_groups)>registration["execution_budget"]["max_training_group_examples_per_fold"]: raise ContractError("TRAINING_GROUP_BUDGET_EXCEEDED",{"fold":fold})
        state,_=fit_ridge(train_groups,features); state["fold"]=fold; state["model_version"]=registration["experiment_name"]
        predictions=predict(validation_groups,state)
        for x in predictions: x.update(fold=fold,model_version=registration["experiment_name"])
        selections,cohorts=select(predictions,fold)
        preprocessing.append({k:state[k] for k in ("fold","features","medians","means","population_standard_deviations","all_missing_training_features","zero_variance_training_features","canonical_json_sha256")})
        models.append(state); all_predictions.extend(predictions); all_selections.extend(selections); all_cohorts.extend(cohorts)
        for partition,groups in (("train",train_groups),("validation",validation_groups)):
            for g in groups:
                mappings.extend({"fold":fold,"partition":partition,"canonical_observation_id":i,"stable_group_identity":g["stable_group_identity"]} for i in g["source_canonical_observation_ids"])
        audits.append({"fold":fold,"status":"PASSED","train_rows":len(train_ids),"validation_rows":len(valid_ids),"train_groups":len(train_groups),"validation_groups":len(validation_groups),"purge_embargo":"PASSED","training_weight_values":[1.0]})
    # These immutable pre-outcome files are finalized and hashed before outcomes are joined.
    write(a.output_dir/"fold_preprocessing.json",preprocessing); write(a.output_dir/"model_artifacts.json",models)
    write(a.output_dir/"development_group_predictions.json",all_predictions); write(a.output_dir/"development_group_selections.json",all_selections)
    pre_hashes={name:file_hash(a.output_dir/name) for name in ("fold_preprocessing.json","model_artifacts.json","development_group_predictions.json","development_group_selections.json")}
    write(a.output_dir/"pre_outcome_hashes.json",pre_hashes)
    results=[]
    for fold,_,valid_ids in sorted(fold_members):
        released=[]
        by_id={x["canonical_observation_id"]:x["stable_group_identity"] for x in mappings if x["fold"]==fold and x["partition"]=="validation"}
        grouped={}
        for identifier in valid_ids:
            value=rows[identifier].get("return_5d"); identity=by_id[identifier]; grouped.setdefault(identity,[]).append(value)
        selection=[x for x in all_selections if x["fold"]==fold]
        template={x["stable_group_identity"]:x for x in selection}
        for identity,values in grouped.items():
            group=dict(template[identity]); group["target"]=sum(float(x) for x in values)/len(values) if all(x is not None for x in values) else None; released.append(group)
        results.append(evaluate(selection,released,fold))
    complete=all(x["status"]=="COMPLETE" for x in results)
    if complete:
        selected=sum(x["selected_gross_return"] for x in results)/3; baseline=sum(x["baseline_gross_return"] for x in results)/3; difference=selected-baseline
        screen={"status":"PASS" if difference>0 and sum(x["selected_minus_baseline"]>0 for x in results)>=2 else "FAIL",
                "aggregate_selected_gross_return":selected,"aggregate_baseline_gross_return":baseline,"aggregate_selected_minus_baseline":difference,
                "positive_fold_count":sum(x["selected_minus_baseline"]>0 for x in results),"fold_weight":1/3}
    else: screen={"status":"NOT_EVALUABLE","reason":"AT_LEAST_ONE_FOLD_BLOCKED"}
    write(a.output_dir/"group_construction_audit.json",audits); write(a.output_dir/"group_row_mapping.json",mappings)
    write(a.output_dir/"development_results.json",{"execution_status":"COMPLETE" if complete else "BLOCKED","development_screen":screen,"folds":results,"cohorts":all_cohorts,"portfolio_curve_computed":False})
    elapsed=time.monotonic()-started; usage=resource.getrusage(resource.RUSAGE_SELF)
    execution={"execution_status":"COMPLETE" if complete else "BLOCKED","registration_sha256":file_hash(a.registration),"authorization_sha256":file_hash(a.authorization),"implementation_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
      "fold_fits":3,"configuration_count":1,"holdout_content_access":False,"historical_oof_used":False,"target":TARGET,"input_provenance":provenance,"canonical_loading":load_counts,
      "resource_usage":{"wall_clock_seconds":elapsed,"max_rss_kib":usage.ru_maxrss,"cpu_user_seconds":usage.ru_utime,"cpu_system_seconds":usage.ru_stime,"cpu_limit":2,"memory_limit_gib":4,"wall_clock_limit_minutes":30}}
    write(a.output_dir/"execution_provenance.json",execution)
    for source in (a.registration,a.authorization): shutil.copyfile(source,a.output_dir/source.name)
    summary=["# Alpha Atlas V4 group-return regression", "",f"- Execution: `{execution['execution_status']}`.",f"- Development screen: `{screen['status']}`.","- Fits: exactly 3; configuration count: 1.","- Holdout content access: `false`.","- Historical run `35923707545-1`: `COMPLETE — UNFAVORABLE FINDINGS` (unchanged).",""]
    for result in results: summary.append(f"- Fold {result['fold']}: `{result['status']}`; selected `{result.get('selected_gross_return')}`; baseline `{result.get('baseline_gross_return')}`; difference `{result.get('selected_minus_baseline')}`.")
    (a.output_dir/"SUMMARY.md").write_text("\n".join(summary)+"\n")
    return execution

def main()->int:
    p=argparse.ArgumentParser()
    for name in ("registration","authorization","canonical","plan","manifest","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True); code=0
    try: execute(a)
    except Exception as exc:
        code=2; details=getattr(exc,"details",{}); write(a.output_dir/"execution_provenance.json",{"execution_status":"FAILED","reason_code":getattr(exc,"code",type(exc).__name__),"details":details,
          "fits_started":details.get("fits_started",0),"fits_completed":details.get("fits_completed",0),"predictions_written":details.get("predictions_written",False),
          "evaluation_executed":details.get("evaluation_executed",False),"holdout_content_access":False})
        concise={k:details.get(k) for k in ("summary","affected_group_assignments","first_issue","fits_started","fits_completed","predictions_written","evaluation_executed","holdout_content_access") if k in details}
        (a.output_dir/"SUMMARY.md").write_text(f"# Execution failed\n\n- Reason: `{getattr(exc,'code',type(exc).__name__)}`.\n- Actionable evidence: `{concise}`.\n- Full evidence: `execution_provenance.json` and `timing_compatibility_audit.json`.\n")
        print(f"ACTIONABLE FAILURE: {getattr(exc,'code',type(exc).__name__)}: {concise}",file=sys.stderr)
    files=sorted(x for x in a.output_dir.iterdir() if x.is_file() and x.name!="SHA256SUMS")
    (a.output_dir/"SHA256SUMS").write_text("".join(f"{file_hash(x)}  {x.name}\n" for x in files))
    return code

if __name__=="__main__": raise SystemExit(main())
