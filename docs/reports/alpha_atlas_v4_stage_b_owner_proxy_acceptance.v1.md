# Alpha Atlas V4 Stage B owner proxy acceptance v1

On 2026-10-07, the owner accepted **AAPL→XLK as an experimental context proxy** for the fixed 2026-10-20 Stage B verification.

The acceptance:

- binds clarification content SHA-256 `8f793c3cecc189f294fc911a70c71ae393f95c623173474a5c69477227c07ddb`;
- binds fixture content SHA-256 `b22907c9036e3f7476ffa35e6601caaefb810e311050f6457d7d5ff4b3041885`;
- does not claim AAPL is an XLK constituent or establish AAPL's sector classification;
- authorizes merging and deploying the reviewed package to `moneybot-market-stream`;
- authorizes preparing a post-deploy authorization bound to the actually observed deployed revision; and
- expressly does **not** authorize Stage B execution, the pilot, training, or scoring.

The accepted proxy binding is `alpha_atlas_v4_stage_b_sector_proxy_binding.accepted.v2.json`. Operational validation requires this accepted binding and, separately, an eventual execution authorization that explicitly binds its content hash.

## Deployment and post-deploy authorization

No deployment API or authenticated Render control plane is available in this repository environment, so deployment is authorized but not claimed as performed. After the reviewed revision is actually deployed, run the following **on that deployed checkout** to generate a non-approved authorization bound to its observed `HEAD`:

```text
python -m scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization --deployed-commit "$(git rev-parse HEAD)" --output /var/data/moneybot-stage-b/authorization/stage-b-2026-10-20.prepared.json
```

The command performs no network operation and reads no credentials. Its output remains `PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED`, with `execution_gate_usable: false`. The owner must separately review and approve a subsequent execution authorization before the operational runner can proceed.

Only after that separate execution approval, the registered manual command would use
the accepted binding (not the superseded proposed binding):

```text
python -m scripts.run_alpha_atlas_v4_stage_b_operational --authorization <SEPARATELY_APPROVED_AUTHORIZATION.json> --approved-authorization-sha256 <EXTERNALLY_PINNED_SHA256> --sector-context docs/reports/alpha_atlas_v4_stage_b_sector_proxy_binding.accepted.v2.json --config docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json --fixture docs/reports/alpha_atlas_v4_stage_b_verification_manifest.v3.json --fixture-file-sha256 c7d06f54aa18d4c3bd52c29e9c7423605e6919a076f29ba24dcfa7e61eecf113 --fixture-content-sha256 b22907c9036e3f7476ffa35e6601caaefb810e311050f6457d7d5ff4b3041885 --session 2026-10-20 --output /var/data/moneybot-stage-b/run/stage-b-result.json
```

This command is documented only; it was not run.
