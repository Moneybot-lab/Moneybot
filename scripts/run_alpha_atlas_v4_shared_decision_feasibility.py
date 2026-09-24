#!/usr/bin/env python3
"""Run the metadata-only shared-decision review on the pinned Actions archive."""
from __future__ import annotations

import argparse, hashlib, io, json, re, stat, subprocess, sys, zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
import ijson

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from moneybot.services.alpha_atlas_v4_shared_decision_feasibility import assess_partition, cohort_distribution, split_conflicts, utc
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash
from moneybot.services.market_data_providers import ExchangeCalendar

RUN_ID=34689216730; ATTEMPT=1; HEAD="1f8f46db584dff0881273bdeae1c56c1a8a016c5"
REPOSITORY="Moneybot-lab/Moneybot"; IMPLEMENTATION_COMMIT="18f6148"
ARTIFACT_ID=10296724235; ARTIFACT_NAME="track-b-offline-output"
ARCHIVE_DIGEST="sha256:017cdd8a8b30917e6e2f958e3cba1ae99827e002434b97f9de40c2c4ab871ef9"
CANONICAL_SHA="506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e"
PLAN_SHA="f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a"
PLAN_SEMANTIC="bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8"
FIELDS={"canonical_observation_id","symbol","ticker","event_date","label_horizon_sessions","decision_at","feature_cutoff_at","entry_at","exit_at","label_start_at","feature_family_source_at","feature_family_available_at","staleness_status","snapshot_available_at","snapshot_constructed_at","reconstruction_lineage","point_in_time_symbol_id"}

class ReviewError(ValueError):
    def __init__(self,code): super().__init__(code); self.code=code

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def one(root,pattern):
    found=[x for x in root.rglob(pattern) if x.is_file()]
    if len(found)!=1: raise ReviewError(f"INPUT_MISSING_OR_AMBIGUOUS:{pattern}:{len(found)}")
    return found[0]

def validate_and_extract(run_path,listing_path,archive,destination):
    run=json.loads(run_path.read_text()); listing=json.loads(listing_path.read_text())
    if (run.get("id"),run.get("run_attempt"),run.get("head_sha"))!=(RUN_ID,ATTEMPT,HEAD): raise ReviewError("SOURCE_RUN_ID_ATTEMPT_OR_COMMIT_MISMATCH")
    if (run.get("repository") or {}).get("full_name")!=REPOSITORY: raise ReviewError("SOURCE_REPOSITORY_MISMATCH")
    if run.get("conclusion")!="success": raise ReviewError("SOURCE_RUN_NOT_SUCCESSFUL")
    matches=[x for x in listing.get("artifacts",[]) if x.get("id")==ARTIFACT_ID]
    if len(matches)!=1: raise ReviewError("SOURCE_ARTIFACT_ID_MISSING_OR_AMBIGUOUS")
    item=matches[0]
    if item.get("name")!=ARTIFACT_NAME: raise ReviewError("SOURCE_ARTIFACT_NAME_MISMATCH")
    if item.get("expired") is not False: raise ReviewError("SOURCE_ARTIFACT_EXPIRED")
    if item.get("digest")!=ARCHIVE_DIGEST: raise ReviewError("SOURCE_ARTIFACT_METADATA_DIGEST_MISMATCH")
    if "sha256:"+sha(archive)!=ARCHIVE_DIGEST: raise ReviewError("DOWNLOADED_ARCHIVE_DIGEST_MISMATCH")
    destination.mkdir(parents=True,exist_ok=True); total=0
    with zipfile.ZipFile(archive) as z:
      if len(z.infolist())>10000: raise ReviewError("ARCHIVE_MEMBER_COUNT_LIMIT")
      for info in z.infolist():
        path=PurePosixPath(info.filename); mode=info.external_attr>>16
        if path.is_absolute() or any(x in ("",".","..") for x in path.parts) or "\\" in info.filename or stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR): raise ReviewError("UNSAFE_ARCHIVE_MEMBER")
        total+=info.file_size
        if info.file_size>2*1024**3 or total>4*1024**3: raise ReviewError("ARCHIVE_SIZE_LIMIT")
        target=destination.joinpath(*path.parts)
        if info.is_dir(): target.mkdir(parents=True,exist_ok=True); continue
        target.parent.mkdir(parents=True,exist_ok=True)
        with z.open(info) as source,target.open("wb") as output:
          while chunk:=source.read(1024*1024): output.write(chunk)
    return item,archive.stat().st_size

def projected_rows(path,needed):
    rows={}
    with path.open("rb") as handle:
      for line in handle:
        # Membership is the only field inspected for non-development lines;
        # holdout rows are never decoded into JSON objects.
        match=re.search(rb'"canonical_observation_id"\s*:\s*"([^"\\]+)"',line)
        if not match: continue
        identifier=match.group(1).decode("utf-8")
        if identifier not in needed: continue
        row=next(ijson.items(io.BytesIO(line),""))
        rows[identifier]={key:row.get(key) for key in FIELDS if key in row}
    if set(rows)!=needed: raise ReviewError(f"DEVELOPMENT_ROWS_MISSING:{len(needed-set(rows))}")
    return rows

def execute(args,item,archive_bytes):
    canonical=one(args.extract_dir,"flat_feature_store/all.jsonl"); plan_path=one(args.extract_dir,"challenger_split_plan.json"); manifest_path=one(args.extract_dir,"challenger_suite_manifest.json")
    actual={"canonical":sha(canonical),"split_plan":sha(plan_path)}
    if actual!={"canonical":CANONICAL_SHA,"split_plan":PLAN_SHA}: raise ReviewError("FROZEN_INPUT_BYTE_HASH_MISMATCH")
    plan=json.loads(plan_path.read_text()); embedded=plan.get("plan_sha256"); recomputed=canonical_json_hash({k:v for k,v in plan.items() if k!="plan_sha256"})
    if embedded!=PLAN_SEMANTIC or recomputed!=PLAN_SEMANTIC: raise ReviewError("SPLIT_PLAN_SEMANTIC_HASH_MISMATCH")
    development=set(map(str,plan.get("train_canonical_observation_ids") or [])); holdout=set(map(str,plan.get("test_canonical_observation_ids") or []))
    manifest=json.loads(manifest_path.read_text()); folds=[x for x in manifest.get("walk_forward_windows",[]) if x.get("usable")]
    assignments=[]; needed=set()
    for fold in folds:
      number=int(fold["fold_index"])
      for partition,key,fallback in (("train","train_canonical_observation_ids","train_ids"),("validation","validation_canonical_observation_ids","validation_ids")):
        ids=list(map(str,fold.get(key,fold.get(fallback,[]))))
        if set(ids)-development or set(ids)&holdout: raise ReviewError("HOLDOUT_OR_NONDEVELOPMENT_MEMBERSHIP")
        assignments.append((number,partition,ids)); needed.update(ids)
    rows=projected_rows(canonical,needed); calendar=ExchangeCalendar(); mappings=[]; partitions=[]
    for fold,partition,ids in assignments:
      subset=[rows[x] for x in ids]; boundaries={}
      for row in subset:
        entry=utc(row["entry_at"]); session=calendar.local_date(entry); prior=calendar.previous_session(session)
        boundaries[(row.get("label_horizon_sessions"),str(row.get("entry_at")),str(row.get("exit_at")))]=calendar.session_close(prior).isoformat()
      result=assess_partition(subset,boundaries)
      for value in result.pop("opportunities"):
        value.update(fold=fold,partition=partition); mappings.append(value)
      distribution=cohort_distribution([x for x in mappings if x["fold"]==fold and x["partition"]==partition])
      partitions.append({"fold":fold,"partition":partition,**result,"cohort_distribution":distribution})
    conflicts=[]
    for fold in sorted({x[0] for x in assignments}):
      train=[rows[x] for f,p,ids in assignments if f==fold and p=="train" for x in ids]
      valid=[rows[x] for f,p,ids in assignments if f==fold and p=="validation" for x in ids]
      boundary=min(utc(x["decision_at"]) for x in valid).isoformat() if valid else None
      conflicts.append({"fold":fold,**split_conflicts(train,valid,boundary)})
    windows=Counter(tuple(x["opportunity_key"][1:]) for x in mappings)
    unknown=sum(x["dispositions"]["UNKNOWN_AVAILABILITY_OR_FRESHNESS"] for x in partitions)
    classification="NOT_SUPPORTED_BY_SAVED_EVIDENCE" if unknown else "FEASIBLE_FOR_NEW_REGISTRATION"
    provenance={"execution_status":"COMPLETE","feasibility_status":classification,"analyzer_merged_commit":IMPLEMENTATION_COMMIT,"workflow_revision":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"source":{"repository":REPOSITORY,"run":RUN_ID,"attempt":ATTEMPT,"head_sha":HEAD,"artifact_id":ARTIFACT_ID,"artifact_name":ARTIFACT_NAME,"archive_digest":ARCHIVE_DIGEST,"archive_bytes":archive_bytes,"artifact_api_size_bytes":item.get("size_in_bytes"),"expired":item.get("expired")},"inputs":{"canonical":{"bytes":canonical.stat().st_size,"sha256":actual["canonical"]},"split_plan":{"bytes":plan_path.stat().st_size,"sha256":actual["split_plan"],"embedded_semantic_sha256":embedded,"recomputed_semantic_sha256":recomputed}},"scope":{"development_metadata_only":True,"holdout_content_access":False,"outcomes_access":False,"training":False,"predictions":False,"performance":False,"provider_queries":False}}
    report={"schema_version":"alpha-atlas-v4-shared-decision-cohort-feasibility.v1","execution_status":"COMPLETE","classification":classification,"common_boundary":{"status":"PROPOSED_OPERATIONAL_CONVENTION_NOT_VERIFIED_HISTORICAL_BEHAVIOR","rule":"official close of XNYS session immediately preceding entry_at"},"partitions":partitions,"execution_windows":{"unique":len(windows),"fold_partition_appearances":len(mappings),"repeated_appearances":sum(v-1 for v in windows.values())},"split_conflicts":conflicts,"selection_limitation":"min(5, eligible) equals full eligible baseline when eligible <= 5","availability_interpretation":"source event/source-bar time alone does not prove assembled snapshot availability; unsupported availability is UNKNOWN"}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"shared_decision_cohort_feasibility.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (args.output_dir/"metadata_opportunity_snapshot_cohort_mapping.json").write_text(json.dumps(mappings,indent=2,sort_keys=True)+"\n")
    (args.output_dir/"input_verification_and_provenance.json").write_text(json.dumps(provenance,indent=2,sort_keys=True)+"\n")
    lines=["# V4 shared-decision cohort feasibility","",f"- Execution: `COMPLETE`.",f"- Feasibility: `{classification}`.","- Boundary: prior XNYS session official close (`PROPOSED`, not verified historical behavior).","- Holdout/outcomes/training/predictions/performance/provider queries: `false`.",""]
    for p in partitions: lines.append(f"- Fold {p['fold']} {p['partition']}: {p['opportunity_count']} opportunities; dispositions `{p['dispositions']}`; cohort bins `{p['cohort_distribution']['bins']}`.")
    (args.output_dir/"shared_decision_cohort_feasibility.md").write_text("\n".join(lines)+"\n")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--run-metadata",type=Path,required=True); p.add_argument("--artifact-metadata",type=Path,required=True); p.add_argument("--archive",type=Path,required=True); p.add_argument("--extract-dir",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); args=p.parse_args(); args.output_dir.mkdir(parents=True,exist_ok=True)
    try:
      item,size=validate_and_extract(args.run_metadata,args.artifact_metadata,args.archive,args.extract_dir); execute(args,item,size); code=0
    except Exception as exc:
      code=2; reason=getattr(exc,"code",type(exc).__name__); payload={"execution_status":"FAILED","feasibility_status":"NOT_EVALUATED","reason_code":reason,"holdout_content_access":False,"training":False,"predictions":False,"provider_queries":False,"performance":False}; (args.output_dir/"input_verification_and_provenance.json").write_text(json.dumps(payload,indent=2)+"\n"); (args.output_dir/"shared_decision_cohort_feasibility.json").write_text(json.dumps(payload,indent=2)+"\n"); (args.output_dir/"shared_decision_cohort_feasibility.md").write_text(f"# V4 shared-decision cohort feasibility\n\n- Execution: `FAILED`.\n- Feasibility: `NOT_EVALUATED`.\n- Reason: `{reason}`.\n")
    files=sorted(x for x in args.output_dir.iterdir() if x.is_file() and x.name!="SHA256SUMS"); (args.output_dir/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in files)); return code

if __name__=="__main__": raise SystemExit(main())
