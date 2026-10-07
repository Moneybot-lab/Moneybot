# Alpha Atlas V4 Stage B operational-verification setup package v1

**Status:** `PREPARED_FOR_REVIEW / NOT_AUTHORIZED / NOT_EXECUTED`  
**Fixture:** AAPL (stock), SPY (market context), XLK (sector context)  
**Attempts:** 5 expected; 18 absolute maximum; every attempt charged to the persistent 3,789-attempt cumulative ledger.  
**Storage recommendation:** 3 GB-decimal Render disk on `moneybot-market-stream`, backed up to Amazon S3 Standard in `us-east-1` with Versioning and 180-day Governance Object Lock.  
**Known incremental price:** $0.75009355/month while the maximum Stage B backup is retained; $4.50166976 over six months including bounded S3 operations and one maximum restore/egress, before tax and account adjustments. No compute upgrade is proposed.

This document is the readable rendering of `alpha_atlas_v4_stage_b_setup_package.v1.json`. The JSON is canonical for arithmetic and acceptance fields.

## Preserved Stage A evidence

Hosted synthetic run `36737873047-1` passed with seven synthetic attempts, zero live requests, 75-session synthetic windows, an eligible synthetic handoff, and reported temporary primary/backup verification plus isolated restore. Real acquisition remained unavailable. Uploaded `report.json` SHA-256 was `ccd9a80839cd10dfe2beb1ad01b1e8779b010037c867923834b6bfa914726cb0`.

The uploaded artifact contained `report.json`, `report.md`, `run.log`, and `SHA256SUMS`; it **did not** contain temporary source, backup, or restore objects. This does not prove operational storage, backup, or retention.

## Verification manifest

The proposed manifest is `alpha_atlas_v4_stage_b_verification_manifest.v1.json`, content SHA-256 `1e59160a60c0f2db6e8f268a8bc6f7eb30db7444435b8a8bd59582c77126d33b`.

The deterministic rule selects the lexically first symbol in the pre-registered `active_security` case in `probe_plan()` without consulting its results: **AAPL**. It retains the pre-registered shared market and sector contexts **SPY** and **XLK**. This is outcome-independent and does not use scores, returns, or retrieval success. A one-stock fixture exercises plumbing; it is not representative and neither selects nor approves the later pilot universe.

The proposed session is 2026-10-05. Its fixed 75-prior-XNYS-session window is 2026-06-17 through 2026-10-02. If that date is missed, this package forbids execution: a new eligible date and window must be bound and the changed manifest hash reviewed.

### Identity and mapping gates

* SEC filing evidence identifies Apple issuer CIK `0000320193`, its common stock ticker AAPL, and Nasdaq listing. CIK is an issuer identifier—not permanent share-class identity. Request 4 must supply an unambiguous provider security identity before handoff.
* State Street holdings observed 2026-09-30 support the proposed AAPL→XLK relation as of that date. They do not prove effectiveness on 2026-10-05. An authoritative effective-date confirmation must be recorded and hashed **before request 1**, or the run stops.
* These pending gates are explicit; they are not silently bypassed. The current planner cannot stage an unresolved security identity, so the narrowly specified planner change below is required before Stage B.

## Ordered request and retry budget

| Order | Dependency | Request | Expected | Hard allocation |
|---:|---|---|---:|---:|
| 1 | AAPL raw history | `GET /v2/aggs/ticker/AAPL/range/1/day/2026-06-17/2026-10-02`, `adjusted=false` | 1 | 3 |
| 2 | SPY raw history | same fixed window | 1 | 3 |
| 3 | XLK raw history | same fixed window | 1 | 3 |
| 4 | dated identity | `GET /v3/reference/tickers/AAPL?date=2026-10-05` | 1 | 3 |
| 5 | split lineage | `GET /stocks/v1/splits`, 2026-06-17 through 2026-10-05 | 1 page | 2 pages × 3 attempts = 6 |
| **Total** | | | **5** | **18** |

The hard formula is `3 histories × 3 + 1 identity × 3 + 2 split pages × 3 = 18`. A retry is inside—not in addition to—the 18. A remaining `next_url` after split page two is `INCOMPLETE_PAGINATION` and stops the run. There is no automatic repeat and an incomplete run receives no fresh 18-attempt allocation.

Each history response is limited to 262,144 bytes, identity to 32,768 bytes, and each split response to 262,144 bytes. The Stage B retained-evidence cap is **4,067,328 bytes (3.87890625 MiB) independently for primary and backup**, or **8,134,656 bytes combined**. Temporary staging and isolated restore are workspace, not retained-evidence allowance.

Acquisition and retries stop at 07:30 America/New_York. 07:30–07:45 is validation/handoff only; binding is 07:45, assembly 07:45–08:30, durable readiness 08:40 inclusive, and the decision boundary 08:45.

## One storage and backup configuration

### Primary

Attach one **3 GB decimal** Render persistent disk to `moneybot-market-stream` at `/var/data`. Set `MONEYBOT_PERSISTENT_DATA_DIR=/var/data/moneybot-stage-b`; use research root `/var/data/moneybot-stage-b/alpha_atlas_v4/stage-b`. Three decimal GB is 2.794 GiB and exceeds the registered 2 GiB operating-capacity requirement. Two decimal GB would be only 1.863 GiB.

The existing web-service 1 GB disk at `/var/data` is not reusable by the worker. Its seven-day snapshots do not satisfy 180-day backup. Its mount does not establish a worker mount or a `MONEYBOT_PERSISTENT_DATA_DIR` binding.

### Backup

Create a dedicated **Amazon S3 Standard, `us-east-1`** bucket during separately authorized setup. Enable Versioning and Object Lock at creation; configure Governance-mode default retention of 180 days, SSE-S3, and Block Public Access. Use prefix `alpha-atlas-v4/stage-b/<verification-id>/`.

The acquisition role is prefix-scoped to put, read back, list, and inspect retention/version IDs, with explicit denial of delete/version-delete and `s3:BypassGovernanceRetention`. A distinct restore role is read-only. Restore only to `/var/data/moneybot-stage-b/restore/<verification-id>`. Temporary staging may be removed only after immutable primary, backup, and restore receipts verify checksums; retained evidence cannot be cleaned before 180 days.

The existing code only simulates backup between local `ImmutableStore` roots. Before Stage B, implement an S3 adapter that publishes versioned objects with Object Lock headers, records S3 version IDs and checksums, reads back, and restores to the isolated directory. This package does not claim that adapter exists.

## Costs

| Item | Basis | Monthly | Six months / bounded operation |
|---|---|---:|---:|
| Render worker disk | 3 GB decimal × $0.25/GB-month | $0.75000000 | $4.50000000 |
| S3 retained maximum | 0.004067328 decimal GB × $0.023/GB-month | $0.00009355 | $0.00056130 |
| S3 PUT/COPY/LIST | at most 128 × $0.005/1,000 | — | $0.00064000 |
| S3 GET | at most 256 × $0.0004/1,000 | — | $0.00010240 |
| Maximum restore egress assumption | 0.004067328 GB × $0.09/GB | — | $0.00036606 |
| **Known arithmetic** | excludes tax/account adjustments | **$0.75009355** | **$4.50166976** |

One-time infrastructure setup fee is assumed $0 from published service pricing; setup labor is not priced. The existing $7 worker, $25 web service, and $200 Massive subscription are sunk charges, not incremental costs. Incremental compute is conditionally $0 only if Stage B observes acceptable headroom.

No free tier or included transfer is assumed. Exact charged total remains unresolved until an approved AWS account/region/billing relationship, taxes/currency/account rates, actual object count/route, and Render billing/proration are known. An unexpected billable requirement stops the check.

Official documentation verified 2026-10-01: [Render disks](https://render.com/docs/disks), [Render pricing](https://render.com/pricing), [S3 pricing](https://aws.amazon.com/s3/pricing/), [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html), and [S3 Versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html).

## Massive endpoint and retention evidence

The active Stocks Advanced Individual subscription at $200/month is account evidence already supplied. Public [pricing](https://massive.com/pricing) and documentation for [custom aggregates](https://massive.com/docs/rest/stocks/aggregates/custom-bars), [ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview), and [splits](https://massive.com/docs/rest/stocks/corporate-actions/splits) were verified 2026-10-01. Public plan descriptions are not proof of this account's endpoint behavior, fair-use capacity, or retention rights. Stage B measures endpoint access; it cannot infer storage rights from success.

Unsent narrow clarification: **“Under my individual Stocks Advanced subscription, may I retain immutable raw responses from Custom Bars, Ticker Overview, and Splits, plus derived snapshots and one versioned backup copy, for 180 days solely for personal prospective reliability research?”**

## Setup and execution runbook

### A. Separately authorize setup and implementation

1. Implement the Stage-B-only identity staging rule, injected Massive transport, S3 adapter, authorization/hash gates, telemetry, and result renderer. Keep Stage A synthetic-only and keep the worker's existing start command unchanged.
2. In Render, attach a 3 GB disk to **`moneybot-market-stream`**, mounted at `/var/data`; set `MONEYBOT_PERSISTENT_DATA_DIR=/var/data/moneybot-stage-b`. Do not touch the web service disk.
3. Create the dedicated S3 bucket/roles exactly as above. Add secret names `ALPHA_ATLAS_V4_MASSIVE_API_KEY`, `ALPHA_ATLAS_V4_S3_BUCKET`, `ALPHA_ATLAS_V4_S3_REGION`, and workload-role credentials/configuration—never values—to the worker's research one-off-job environment. Do not expose them to Stage A.
4. Deploy only after review; do not alter the continuously running stream worker start command. Use a Render one-off job or separately controlled process on the worker image and disk.
5. Preflight without provider requests: verify authorization file, exact manifest hash, bound contract hashes, eligible session/calendar, effective mapping, disk root/permissions/free space, S3 bucket versioning/Object Lock/roles, clock, cumulative ledger integrity, and stream health baseline. Any failure stops before request 1.

**No operational command exists today.** Stage A intentionally has no live transport. The implementation task should add a non-scheduled module `scripts.run_alpha_atlas_v4_stage_b_verification` requiring explicit manifest and authorization files; until reviewed and implemented, no command may be substituted and the synthetic CLI must not be bypassed.

### B. Separately authorize one bounded run

Run the reviewed one-off command on `moneybot-market-stream`; reserve each attempt durably before transmission. Stop network activity at 07:30. Persist sanitized raw response/receipt revisions, validate split-adjusted windows, publish primary evidence, copy/version-lock backup, checksum read-back, restore into the isolated directory, then expose only the validated cache-only handoff. Retrieve the compact result JSON/Markdown/log/checksums without deleting runtime objects.

Never auto-repeat. An uncertain, failed, or incomplete attempt remains charged. Recovery is read-only reconciliation first; another request or run needs new authorization and remaining-ledger review.

### C. Measurements produced by that run

Record endpoint completion, response sizes, receipt/provenance, identity/split/window consistency, acquisition duration, worker CPU/RSS and stream interference, primary integrity, S3 version/retention/checksum, isolated restore, and handoff disposition. These are currently `NOT_TESTED`—they are outputs of Stage B, not setup prerequisites.

## Acceptance and stopping

Each JSON result requirement uses `PASS`, `FAIL`, `UNKNOWN`, or `NOT_TESTED`; overall PASS requires every required item PASS. Stop on authorization/manifest mismatch, unexpected billing, deadline or budget, incomplete pagination, missing identity/mapping/splits, corrupt persistence/restore, unacceptable stream interference, secret leakage, or an unauthorized destination.

A small PASS would not prove full-pilot throughput or 180 elapsed days of retention. It would not approve the <=50-stock universe, start the pilot, authorize training, or establish predictive advantage.

## Approval boundary

The next approval would cover **setup/implementation only**: the narrowly listed code, one 3 GB worker disk, one locked/versioned S3 bucket and least-privilege roles, and deployment without changing production scheduling or start commands. A second explicit approval would be required for the one Stage B run of 5 expected / 18 maximum live attempts and 4,067,328 bytes per retained copy. The pilot remains disabled.
