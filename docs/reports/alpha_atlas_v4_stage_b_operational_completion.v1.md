# Alpha Atlas V4 Stage B operational implementation supplement v1

**Status:** `IMPLEMENTED / OFFLINE TESTED / FIXTURE EXPIRED / LIVE NOT AUTHORIZED`

## Narrow findings and fixes

The prior implementation had an injected S3 protocol exercised only by an in-memory fake; it did not contain an authenticated SDK adapter or an operational command. It also recorded S3 operations after calls rather than durably reserving them first. The fixed code now provides:

- A lazy boto3 client factory using only explicit access key/secret/optional session token, `us-east-1`, short timeouts, and `total_max_attempts=1`. It does not use the SDK credential chain, metadata service, or STS.
- A non-scheduled operational command that requires a separately supplied `APPROVED` authorization and external authorization hash. Local hashes, date, mapping, root and capacity are checked before closures create clients or read credentials. This task creates no approved authorization.
- Existing-bucket verification for the expected 12-digit account owner, region, Versioning, default AES256 encryption, all four public-access blocks, and at least 180 days of Governance Object Lock.
- Durable `RESERVED` and `TRANSMITTING` records before every SDK call, followed by `SUCCEEDED` or `FAILED`. Restart reconciliation marks a nonterminal operation `UNCERTAIN` and stops; it never repeats an upload automatically.
- Exact version-ID/SHA-256 backup and restore, plus RSS and disk checks before and during acquisition, backup, and restore.

Stage A and Stage B offline commands remain network-free.

## S3 operation reconciliation

| Operation | Expected (12 objects) | Maximum complete path (14 objects) |
|---|---:|---:|
| Bucket identity, region, Versioning, public access, encryption, Object Lock | 6 | 6 |
| Versioned uploads | 12 | 14 |
| Version/retention `HEAD` verification | 12 | 14 |
| SHA-256 read-back | 12 | 14 |
| Exact-version isolated restore | 12 | 14 |
| **Total** | **54** | **62** |

Fourteen maximum objects reflect the second allowed split page and its receipt. SDK retries are disabled; a failure consumes its reserved operation and stops. Crash reconciliation uses no network operation and blocks manual review. Explicit credentials add zero authentication network calls. Thus the 512 technical cap covers the bounded path with 450 operations of defensive headroom, but it is **not** permission to retry, rerun, or spend.

Massive accounting is unchanged: five expected, eighteen maximum, cumulative 3,789, and no extra identity/preflight call.

At the setup package's 2026-10-01 rates, expected S3 request operations cost approximately `$0.0000768`; the 62-operation maximum costs `$0.0000892`. The prior estimate reserved `$0.0007424` for operations, so it was conservative. Replacing that allowance yields a known six-month maximum of `$4.501016550784`, before account-specific pricing, tax, credits, or billing. No spending is authorized.

## Expired fixture

The preserved fixture remains session **2026-10-05**, acquisition cutoff **07:30 America/New_York**, and window **2026-06-17 through 2026-10-02**. Its acquisition window has passed. The runner rejects it before constructing S3 or Massive clients. It cannot roll, backdate, or execute.

A future authorization must rebind and rehash: session; ordered 75-prior-XNYS dates and endpoints; identity date; split range; effective AAPL→XLK interval/source hash; fixture file/content hashes; any changed setup/contract binding; sector-evidence and operational-configuration hashes; and the authorization's session, bindings, maximum attempts, and externally pinned full hash. No replacement date is selected here.

## Offline command

```bash
python -m scripts.run_alpha_atlas_v4_stage_b_offline \
  --offline-synthetic \
  --output-dir <temporary-output-dir>
```

This validates the complete synthetic path with five synthetic Massive attempts, 54 mock S3 operations, and zero live Massive/AWS requests. Real disk, bucket, permissions, costs, provider access, retention rights, headroom, interference, and restore remain measurements for a separately authorized bounded run—not prerequisites requiring a second provider run.

No provisioning, purchase, deployment, collection, training, scoring, live provider request, or live AWS request occurred.

## Operational entry point and owner setup order

The non-scheduled operational entry point is:

```bash
python -m scripts.run_alpha_atlas_v4_stage_b_operational \
  --authorization <approved.json> \
  --approved-authorization-sha256 <externally-pinned-sha256> \
  --sector-evidence <mapping.json> \
  --config <runtime.json> \
  --output <result.json>
```

It is not runnable for the expired fixture and has not been executed. Owner setup order: (1) attach the 3 GB worker disk and bind the exact research root; (2) configure the pre-existing dedicated S3 destination and restricted roles; (3) install least-privilege S3/Massive secrets without governance bypass; (4) confirm retention permission and effective mapping; (5) rebind and review a future session/window manifest; and (6) separately approve and externally pin one execution authorization before authorizing the bounded run.
