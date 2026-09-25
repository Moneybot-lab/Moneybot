import hashlib, json, subprocess, sys, zipfile

import scripts.run_alpha_atlas_v4_shared_decision_feasibility as runner
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    source=tmp_path/"payload"; (source/"flat_feature_store").mkdir(parents=True); (source/"challenger_suite").mkdir()
    ids=["train","validation"]
    rows=[]
    for identifier,symbol in zip(ids,["A","B"]):
        rows.append({"canonical_observation_id":identifier,"symbol":symbol,"event_date":"2026-01-02","label_horizon_sessions":5,"decision_at":"2026-01-03T02:00:00Z","feature_cutoff_at":"2026-01-03T02:00:00Z","entry_at":"2026-01-05T14:30:00Z","label_start_at":"2026-01-05T14:30:00Z","exit_at":"2026-01-09T21:00:00Z","feature_family_source_at":{"daily":"2026-01-02T21:00:00Z"},"feature_family_available_at":{"daily":"2026-01-02T21:00:00Z"},"staleness_status":"fresh","return_5d":999})
    canonical=source/"flat_feature_store/all.jsonl"; canonical.write_text("".join(json.dumps(x)+"\n" for x in rows))
    plan={"train_canonical_observation_ids":ids,"test_canonical_observation_ids":[]}; plan["plan_sha256"]=canonical_json_hash(plan)
    plan_path=source/"challenger_suite/challenger_split_plan.json"; plan_path.write_text(json.dumps(plan))
    manifest={"walk_forward_windows":[{"usable":True,"fold_index":1,"train_ids":["train"],"validation_ids":["validation"]}]}
    (source/"challenger_suite/challenger_suite_manifest.json").write_text(json.dumps(manifest))
    archive=tmp_path/"source.zip"
    with zipfile.ZipFile(archive,"w") as z:
        for path in source.rglob("*"):
            if path.is_file(): z.write(path,path.relative_to(source))
    run=tmp_path/"run.json"; run.write_text(json.dumps({"id":runner.RUN_ID,"run_attempt":runner.ATTEMPT,"head_sha":runner.HEAD,"conclusion":"success","repository":{"full_name":runner.REPOSITORY}}))
    listing=tmp_path/"artifacts.json"; listing.write_text(json.dumps({"artifacts":[{"id":runner.ARTIFACT_ID,"name":runner.ARTIFACT_NAME,"expired":False,"digest":"sha256:"+sha(archive),"size_in_bytes":archive.stat().st_size}]}))
    return run,listing,archive,canonical,plan_path,plan["plan_sha256"]


def test_valid_metadata_only_invocation(tmp_path,monkeypatch):
    run,listing,archive,canonical,plan,semantic=fixture(tmp_path)
    monkeypatch.setattr(runner,"ARCHIVE_DIGEST","sha256:"+sha(archive)); monkeypatch.setattr(runner,"CANONICAL_SHA",sha(canonical)); monkeypatch.setattr(runner,"PLAN_SHA",sha(plan)); monkeypatch.setattr(runner,"PLAN_SEMANTIC",semantic)
    output=tmp_path/"output"; args=type("A",(),{"extract_dir":tmp_path/"extract","output_dir":output})
    item,size=runner.validate_and_extract(run,listing,archive,args.extract_dir); runner.execute(args,item,size)
    report=json.loads((output/"shared_decision_cohort_feasibility.json").read_text())
    assert report["execution_status"]=="COMPLETE"
    assert report["classification"]=="NOT_SUPPORTED_BY_SAVED_EVIDENCE"
    assert report["partitions"][0]["dispositions"]["UNKNOWN_AVAILABILITY_OR_FRESHNESS"]==1
    assert report["partitions"][1]["dispositions"]["UNKNOWN_AVAILABILITY_OR_FRESHNESS"]==1
    assert json.loads((output/"input_verification_and_provenance.json").read_text())["scope"]["outcomes_access"] is False


def test_structured_expired_source_failure(tmp_path):
    run,listing,archive,*_=fixture(tmp_path); payload=json.loads(listing.read_text()); payload["artifacts"][0]["expired"]=True; listing.write_text(json.dumps(payload))
    try: runner.validate_and_extract(run,listing,archive,tmp_path/"extract")
    except runner.ReviewError as exc: assert exc.code=="SOURCE_ARTIFACT_EXPIRED"
    else: raise AssertionError("expected fail-closed expiration error")


def test_structured_archive_digest_and_missing_input_failures(tmp_path,monkeypatch):
    run,listing,archive,canonical,plan,semantic=fixture(tmp_path)
    monkeypatch.setattr(runner,"ARCHIVE_DIGEST","sha256:"+"0"*64)
    try: runner.validate_and_extract(run,listing,archive,tmp_path/"bad-extract")
    except runner.ReviewError as exc: assert exc.code=="SOURCE_ARTIFACT_METADATA_DIGEST_MISMATCH"
    else: raise AssertionError("expected fail-closed digest error")
    monkeypatch.setattr(runner,"ARCHIVE_DIGEST","sha256:"+sha(archive)); monkeypatch.setattr(runner,"CANONICAL_SHA",sha(canonical)); monkeypatch.setattr(runner,"PLAN_SHA",sha(plan)); monkeypatch.setattr(runner,"PLAN_SEMANTIC",semantic)
    item,size=runner.validate_and_extract(run,listing,archive,tmp_path/"extract")
    (tmp_path/"extract/challenger_suite/challenger_split_plan.json").unlink()
    args=type("A",(),{"extract_dir":tmp_path/"extract","output_dir":tmp_path/"output"})
    try: runner.execute(args,item,size)
    except runner.ReviewError as exc: assert exc.code.startswith("INPUT_MISSING_OR_AMBIGUOUS:challenger_split_plan.json")
    else: raise AssertionError("expected fail-closed missing-input error")


def test_module_entry_point_has_no_inherited_pythonpath():
    environment={"PATH":__import__("os").environ["PATH"]}
    result=subprocess.run([sys.executable,"-m","scripts.run_alpha_atlas_v4_shared_decision_feasibility","--help"],cwd=runner.ROOT,env=environment,capture_output=True,text=True)
    assert result.returncode==0
    assert "--run-metadata" in result.stdout
