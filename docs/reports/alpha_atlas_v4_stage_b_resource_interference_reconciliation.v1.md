# Alpha Atlas V4 Stage B historical resource and interference reconciliation v1

## Purpose and provenance

Complete the historical evidence-review activity recommended by PR #657, confined
to `moneybot-market-stream` on October 7, 2026, approximately 18:55–19:15 MDT
(October 8, 00:55–01:15 UTC). The owner inspected logs through approximately
19:10 MDT (01:10 UTC); the log view and metric window have different endpoints.
This is a documentation-only reconciliation of owner-supplied observations and
read-only repository definitions. Render screenshots, metric samples, outcome
bytes, exact deployed configuration and historical stream snapshots are not
independently available here. No Render API retrieval or screenshot verification
occurred. No artifact-content hash is inferred from a filename.

The owner's bounded filename search outside the replay directory identified only
execution/continuation claims and two outcomes, not a dedicated resource or
interference file. That is not an exhaustive filesystem search or proof that
measurements never existed. No new operational evidence is fabricated.

## Preserved checkpoints

```text
PROVIDER_NORMALIZATION_REPAIR: CLOSED
OFFLINE_SAVED_EVIDENCE_REPLAY: PASS
ISOLATED_REPLAY_HANDOFF: ELIGIBLE
ORIGINAL_LIVE_EXECUTION: FAILED_UNCHANGED
PREMARKET_TIMING: NOT_TESTED
PROSPECTIVE_ELIGIBILITY: NOT_TESTED
PILOT_CONSUMPTION: PROHIBITED
OVERALL_STAGE_B: OPEN
```

PR #656 and the [PR #657 replay closure](alpha_atlas_v4_stage_b_replay_closure.v1.md)
remain closed and unchanged. The reported offline replay processed 75 rows each
for AAPL/SPY/XLK, validated five saved Massive attempts and 91 S3 operations,
and produced an eligible isolated replay handoff with zero new provider/AWS
requests. This task did not rerun it or reinterpret it as prospective success.

## Historical timeline and observations

All timestamps and observations in this section are owner-reported.

| UTC on October 8, 2026 | Event |
|---|---|
| 00:58:01 | Render deployment began |
| 00:58:41 | Service reported live |
| 00:58:49 | Market-stream startup command ran |
| 00:58:50 | Massive streaming worker started, `shadow_mode=True` |
| 00:59:00 | WebSocket authenticated |
| 00:59:00 | Subscriptions active for 65 symbols, including SPY and QQQ |
| 01:02:42.029755+00:00 | `ACQUISITION_FAILED`, `FEATURE_WINDOW_INVALID` |
| 01:02:51.534233+00:00 | `PHASE_FAILURE`, `FEATURE_WINDOW_INVALID` |

The two original immutable outcomes are reported at:

```text
/var/data/moneybot-stage-b/primary/run/outcome-2fcbe1f7499c55235648a1d62627194cb76c5c1befee1faa06af0b18f08828bd.json
/var/data/moneybot-stage-b/primary/run/outcome-aa80870bcd0c4a70a338bb778a36bb75fc2e1f2e1ca4d85917c5eac435490930.json
```

Their approximately 9.5-second separation measures separation of outcome records,
not total execution time. Deployment/startup times are not Stage B start times.
The original live failure and all claims, receipts and ledger records are preserved.

| Monitoring view | Owner observation | Supported limit of interpretation |
|---|---|---|
| Logs | No entries after subscription confirmation through approximately 01:10 UTC | Authentication/subscription activation succeeded; no later disconnect/crash was reported in displayed logs. No proof of continuous delivery, latency or zero interference. Deployment/restart prevents assuming continuity across the beginning of the window. |
| Memory, 512 MB service limit | Approximately 32% normally; brief approximately 40% peak near 19:02–19:03 MDT, then prior range; no sustained saturation visible | Favorable service-level observation, not process peak RSS or a guard-compliance measurement. No derived exact MB/KiB measurement. |
| CPU, 0.5 CPU allocation | Spike near 18:59 MDT/startup; otherwise low; small increase near failure; no sustained saturation visible | No exact utilization, CPU seconds, throttling or per-process attribution available; temporal coincidence does not establish cause. |
| Disk, configured 3 GB limit | Nearly flat, close to 0% on displayed graph | No visible large usage increase/exhaustion; no exact free bytes, reservation behavior or filesystem guard compliance established. |
| Outbound network | One sample per hour; no usable event-specific view | Monthly usage does not assess the short event. Event-specific bandwidth UNKNOWN. |
| Instances | One instance throughout selected interval | No instance-count change observed; this does not prove process continuity or throughput. |
| Inbound stream | No message-count, dropped-message, reconnect-rate or latency measurements supplied | Historical continuity and interference remain UNKNOWN. |

## Registered requirements and evidence gaps

Sources inspected: [setup Markdown](alpha_atlas_v4_stage_b_setup_package.v1.md),
[canonical setup JSON](alpha_atlas_v4_stage_b_setup_package.v1.json),
[owner-bound runtime configuration](alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json),
[continuation approval](alpha_atlas_v4_stage_b_continuation_owner_approval.v1.md),
[operational-purpose clarification](alpha_atlas_v4_stage_b_operational_purpose_clarification.v1.json),
the implementation checklist and replay closure. Operational definitions were read
in `moneybot/services/alpha_atlas_v4_stage_b.py`,
`scripts/run_alpha_atlas_v4_stage_b_operational.py` and
`moneybot/services/market_stream.py`; none was executed or modified.

The canonical acceptance schema lists `deadline`, `evidence_budget` and
`worker_interference` among required items, with the rule: **“PASS only when every
required item is PASS; UNKNOWN, NOT_TESTED, or FAIL prevents Stage B acceptance.”**
Setup measurements include **“worker CPU/RSS and stream interference”**; stopping
criteria include **“unacceptable stream interference”**. No numerical CPU,
message-drop, reconnect or latency acceptance threshold is registered in that
setup package. Monitoring observations cannot create one.

Classification below applies to the defined historical review. UNKNOWN means
neither compliance nor violation is established. NOT_APPLICABLE is used only for
the operational-only timing distinction; it does not waive prospective gates.

| Specific requirement / registered bound | Classification | Evidence and missing basis |
|---|---|---|
| Process resource guard: `ru_maxrss <= max_rss_kib`; owner-bound config `393216` KiB | UNKNOWN | `RuntimeResourceGuard` raises `RUNTIME_HEADROOM_EXHAUSTED` only above the bound. CLI constructs it from config. Approximate service percentages are not process samples; exact deployed config and phase RSS missing. |
| Filesystem preflight and phase free-space guard: at least `8_134_656` bytes by default | UNKNOWN | `storage_preflight` and `RuntimeResourceGuard` compute `statvfs.f_bavail * f_frsize`; values below the bound stop. Disk graph gives no such value or phase coverage. |
| Operating capacity: registered 2 GiB primary operating requirement; chosen 3 GB-decimal worker disk | UNKNOWN | Owner reports configured 3 GB, consistent with the recommendation. Runtime mount/quota/access and usable capacity not independently measured; graph alone cannot close the full storage requirement. |
| Retained evidence: primary and backup each at most `4_067_328` bytes; combined at most `8_134_656` bytes | UNKNOWN | Reported replay coverage of 169053 bytes is not a complete accounting of all retained primary/backup evidence or workspace. `PRIMARY_CAP`, `BACKUP_CAP`, `COMBINED_CAP` and checkpoint checks retain exact limits. |
| Finalization reserve: `262_144` bytes; acquisition sublimit `3_805_184` bytes | UNKNOWN | Registered accounting reserves room before new requests. No complete historical accounting demonstrating reserve use was supplied; near-zero disk plot is not that accounting. |
| CPU/RSS headroom and acceptable `worker_interference` | UNKNOWN | No sustained service saturation observed, but no contemporaneous baseline, delivery/lag/drop/reconnect series or workload attribution. No registered numerical CPU/interference threshold to compare. |
| Complete operational runtime/deadline: at most 55 minutes in approved finite interval | UNKNOWN | Runner uses `min(execution_valid_until, started + 55 minutes)` in operational-only mode. Continuation approval ends before `2026-10-09T04:00:00Z` and accepts unknown legacy startup elapsed time. Failure records predate that ceiling but cannot establish start/end, full duration or exact runtime-bound artifact compliance. |
| Historical stream continuity | UNKNOWN | Startup/restart plus authentication and subscription activation are demonstrated as reported; no subsequent errors logged is insufficient for sustained traffic or continuity. |
| Prospective 07:30 acquisition cutoff / prospective snapshot timing within this operational-only observation | NOT_APPLICABLE | Operational-purpose clarification separates this check from premarket timing. Premarket and prospective eligibility remain NOT_TESTED and still open for prospective readiness. |
| Successful end-to-end original Stage B execution | FAIL | Owner-reported original `FEATURE_WINDOW_INVALID` outcomes demonstrate failure; derived replay cannot change it. This is the existing failure, not a newly discovered resource violation. |

No mandatory resource/interference acceptance criterion is conclusively PASS.
Repository evidence conclusively establishes the **definitions and instrumentation**,
not their successful historical application. Favorable monitoring observations
are not formal PASS; missing phase telemetry remains UNKNOWN. Neither outcome
proves completed acquisition/validation/handoff or full operational acceptance.

## Supported conclusions

```text
HISTORICAL_RENDER_RESOURCE_REVIEW: COMPLETED_WITH_LIMITATIONS
SERVICE_LEVEL_CPU_SATURATION: NOT_OBSERVED
SERVICE_LEVEL_MEMORY_SATURATION: NOT_OBSERVED
SERVICE_LEVEL_DISK_EXHAUSTION: NOT_OBSERVED
PROCESS_LEVEL_RESOURCE_GUARD_COMPLIANCE: UNKNOWN
HISTORICAL_STREAM_CONTINUITY: UNKNOWN
STREAM_INTERFERENCE_ACCEPTANCE: UNKNOWN
COMPLETE_OPERATIONAL_RUNTIME_COMPLIANCE: UNKNOWN
ORIGINAL_LIVE_EXECUTION: FAILED_UNCHANGED
OVERALL_STAGE_B: OPEN
```

NOT_OBSERVED describes this limited monitoring view, not acceptance PASS or proof
of zero transient saturation. The historical review is complete; the acceptance
gate stays open. Normalization and replay conclusions are not reopened.

## Smallest remaining verification and authorization boundary

Recommend **reviewing already-preserved timestamped stream snapshots and a
contemporaneous baseline, if they exist**, before any prospective test. Existing
`StreamMetrics.snapshot()` exposes messages received, dropped events, sequence
gaps, reconnect count, slow-consumer events, event-to-Redis lag and Redis write
latency percentiles. Worker `snapshot()` includes connection state, last-message
and connected timestamps, and an updated timestamp. These are in-process
instrumentation definitions, not proof of historical persistence or an available
external endpoint. No new Redis/Render query is issued here. Counters can reset
on restart, and today's values cannot reconstruct this historical interval.

A source/revision/time-bound pre/during/post snapshot series, reset boundaries,
market/session activity context and baseline would let the owner assess delivery,
lag and reconnect/drop changes against the existing qualitative interference
criterion. No arbitrary thresholds are introduced. If no such preserved evidence
exists, historical continuity/interference stays UNKNOWN; a later observation
cannot retroactively certify it. Reviewing owner-supplied preserved exports offline
needs no provider requests, AWS calls, scheduling, deployment or new code.

| Missing observation | Existing instrumentation and possible next evidence | Boundary |
|---|---|---|
| Process peak RSS and exact filesystem free bytes, with phase coverage | `RuntimeResourceGuard` samples process `ru_maxrss` and `statvfs`; preserved run/phase samples, if any, can be compared offline. Read-only process/filesystem observations can be collected independently of acquisition but cannot prove missing historical workload phases. | Any new worker measurement needs separate owner authorization specifying process/root, interval, source revision and observation-only scope. Do not invoke the operational runner merely to obtain samples. |
| Total operational elapsed time and deadline | Existing success-path `telemetry()` emits runtime/CPU/RSS; exact preserved start/end/claim/phase timestamps and approved artifact are needed. Failure path does not establish complete telemetry; unknown legacy time remains disclosed. | Offline preserved-record review first. Any later workload-specific bounded observation needs separate scope/interval/budget approval; no acquisition is presumed necessary or designed here. |
| Complete evidence bytes/reserve compliance | Existing checkpoint accounting and actual inventories/local file sizes can be reconciled read-only if exported. Exact all-copy totals and relevant checkpoints missing. | Existing owner-supplied data can be reviewed offline; new worker enumeration needs separately approved read-only scope. No AWS inventory/read-back or deletion authorized. |
| Stream delivery/latency/drop/reconnect baseline and overlap | Existing stream counters/snapshots can observe the already-running stream without another REST acquisition. Preservation/export availability is unverified. Service CPU/disk graphs cannot substitute. | If historical exports are absent, prospective bounded passive observation eventually needs explicit owner approval for interval, snapshot method, baseline and acceptance interpretation. No restart, resubscription, new provider connection/request, recovery trigger or deployment is authorized. |

New code is unnecessary for this historical reconciliation. Future passive
observation could assess current stream behavior independently of Stage B; it
would not establish Stage B workload interference or retroactively complete the
failed execution. Workload-specific remaining gaps may eventually need a separately
approved bounded test, but this evidence does not justify another acquisition or
full operational run. No test is designed, scheduled or executed by this task.

## Operational restrictions

V4 remains isolated research/shadow-only. No Massive/AWS requests, credential
access, replay/acquisition/continuation, deployment, operational-code or limit
changes, source/receipt/hash/ledger mutation, production routing, pilot use,
training, fitting, scoring, trading, promotion or Stage C occurred or is authorized.
The authorized outputs are documentation, offline integrity checks, and the
GitHub documentation PR. Stop after publication; any new operational verification
requires separate owner authorization.

## Documentation validation

Offline reference/link checks, registered acceptance-key and exact RSS-bound
checks, preservation of all previously completed checklist items and checkpoint
statuses, and changed-file scope checks passed. Original normalization-repair
v1 JSON/Markdown SHA256SUMS both passed. `git diff --check` passed. Prior replay
closure, setup/approval records, operational code and tracked evidence remain
unchanged. No operational or network-dependent tests were run; runtime source
bytes remain unavailable and their checksums were not independently verified.
