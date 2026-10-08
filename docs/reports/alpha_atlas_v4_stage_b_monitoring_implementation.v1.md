# Alpha Atlas V4 Stage B bounded monitoring implementation v1

## Status and scope

```text
MONITOR_IMPLEMENTATION: VERIFIED_OFFLINE
SYNTHETIC_EVIDENCE_GENERATION: PASS
LIVE_PASSIVE_OBSERVATION: NOT_EXECUTED
STREAM_INTERFERENCE_ACCEPTANCE: UNKNOWN
PROCESS_LEVEL_RESOURCE_COMPLIANCE: UNKNOWN
OVERALL_STAGE_B: OPEN
```

Implementation and offline fixtures are complete. Normalization repair (#656),
replay closure (#657), and historical review (#658) remain closed and unchanged.
The original `FEATURE_WINDOW_INVALID` execution remains failed; historical
continuity, resource compliance and interference remain UNKNOWN. Premarket and
prospective eligibility remain NOT_TESTED; pilot use is PROHIBITED.

## Architecture and changed surfaces

`moneybot/services/alpha_atlas_v4_stage_b_monitor.py` adds a separate passive
observer and `scripts/run_alpha_atlas_v4_stage_b_monitor.py` is its thin manual CLI.
No stream processing, subscriptions, health TTL, normal startup, V3/V3.1/Track B,
Stage B acquisition/continuation/replay, or production routing is changed.

The observer reuses `market_stream.py`'s `market-stream.v1` health schema and
existing `StreamMetrics` counters/percentiles: messages by A/AM/T/Q event type,
reconnects, drops, sequence gaps, parse failures, slow consumers, event-to-Redis
lag and Redis write latency p50/p95/p99. It preserves health update, connected and
last-message timestamps and connection state. `RedisMarketStreamState.set_health`
already publishes `moneybot:market:v1:health` with a default 30-second TTL.
The new adapter uses only GET of that key; it does not instantiate the state class,
which also exposes writes/scans. Connection authentication/Redis database selection
may be required by the existing URL; key operations are strictly GET only.
URL configuration is validated before pool/client construction. Only `redis://`
and `rediss://` with userinfo authentication, an unambiguous database path or `db`
query, and verified TLS certificate/CA options are supported. Query options are
allowlisted; unknown, duplicate, blank, operational or conflicting options are
rejected with sanitized errors, even if they match observer defaults. TLS requires
certificate validation and hostname checking; client certificate/key must be paired.
Unix sockets, query authentication, OCSP and protocol/client-identification options
are unsupported. An explicit pool receives parsed settings followed by fixed safety
options; redis-py's URL-over-keyword precedence cannot override them. RESP2 is
fixed, so connection setup uses only necessary AUTH/SELECT (when configured).
Redis library client-identification/health-check commands and retries are disabled.
Connect/read timeouts are two seconds each, and new reads do not start in the
last four seconds of the authorized window.

There is no provider/AWS transport or new WebSocket connection, subscription,
Redis key/channel mutation, database write, service hook or schedule. Offline mode
uses deterministic in-memory local fixtures, a simulated clock and synthetic
resources; it never reads live Redis or `/var/data`. Raw health payloads, URLs,
last-error text, symbols, raw market data and credentials are not archived.

## Exact bounds and attribution

The committed owner-bound runtime config supplies `max_rss_kib=393216`; existing
`COMBINED_CAP=8134656` supplies the free-byte bound. `RuntimeResourceGuard` remains
unchanged. Observations are not a replacement for that guard or a PASS certificate.

Manual observation is capped at 3300 seconds, 360 samples, 2 MiB total evidence,
and 5–60 seconds between samples (recommend 10 seconds). The authorization can
narrow these bounds; sample budget must cover its entire interval. Wall-clock
regression stops capture, and live duration also has a monotonic time bound.
Redis input is limited to 32 KiB after receipt; archived rows to 8192 bytes.
128 KiB is reserved for finalization. RAM is bounded by 360 sanitized rows;
observations are fsynced once per sample. Output capacity checks retain
8134656 free bytes **plus the entire monitoring allowance**, and run before and
during capture. Monitoring evidence must be outside the entire original Stage B
root: `/var/data/moneybot-stage-b-monitoring/<unique-id>`. The authorized filesystem
device must match a mounted `/var/data`; paths alone do not establish persistence.

Linux resource reads select only an explicitly approved PID and require matching
boot ID/start ticks to resist PID reuse. The observer never reads process environment
or command-line arguments and never substitutes its own RSS. Procfs may be isolated
in a Render shell. Even a selected PID match has owner-declared, unverified mapping
to the market-stream worker, so process compliance stays UNKNOWN. Process samples
include start time, RSS/peak KiB and cumulative CPU seconds if accessible; absent
process samples remain null/UNKNOWN. No service/container metric is misrepresented
as a worker measurement.

`statvfs` measures the actual Stage B root in the **observer mount namespace** and
records root, longest applicable mount, device, `st_dev` and free bytes. A matching
owner-supplied device does not prove the market-stream worker shares the namespace;
filesystem compliance remains UNKNOWN unless separately reviewed. Missing/mismatched
mount attribution is explicit. These limitations do not require changing the stream
loop for this implementation.

Current production health has no worker instance ID, PID or source-revision field.
Its source remains `OWNER_BOUND_HEALTH_KEY`, not independently verified worker
identity. Such snapshots are archived with `UNKNOWN_SOURCE_IDENTITY`; conclusive
counter-rate comparison is withheld. Connected timestamp/counter changes still
record apparent restart boundaries. A future independently established identity
binding is required before interpreting valid-source deltas. We do not add an
unrequested production instrumentation change to manufacture that identity.

## Evidence schema and comparisons

Required outputs are `observations.jsonl`, `summary.json`, `report.md`, and
relative-path `SHA256SUMS`. Schema and implementation version are
`alpha-atlas-v4-stage-b-monitor.v1`. JSON is canonical: sorted keys, ASCII escapes,
compact separators and one trailing newline. Synthetic output is byte-deterministic.

| Artifact | Fields / interpretation |
|---|---|
| observations.jsonl | UTC observation time; source type, target/revision and source-binding quality; actual phase at read completion; owner-declared workload marker reference; sampling delay; sanitized health with status/reasons; resource time/source/units/limits and attribution. Missing/malformed data is UNKNOWN, not zero. |
| summary.json | Mode/version/revision; authorization ID and complete canonical hash; exact implementation module hash; actual start/end; phase counts and missing phases; observation count/gaps/delays; reset boundaries; exact final storage bytes; resource bounds; descriptive per-phase counter/message changes, throughput and last cumulative latency percentiles; comparisons against baseline; remaining UNKNOWN gates and fixed pilot prohibition/Stage B OPEN. |
| report.md | Compact evidence disposition and interpretation limits; points reviewers to the detailed summary. |
| SHA256SUMS | SHA-256 of the exact three other files using relative basenames; completion marker published last. |

Phases are explicitly scheduled `BASELINE_BEFORE`, `DURING_AUTHORIZED_TEST` and
`AFTER_TEST`. Missing phases stay UNKNOWN. The observer never starts workload on
phase changes. Supplied execution-marker hashes are references only, not verified
marker bytes: workload association is `OWNER_DECLARED_UNVERIFIED`. No baseline is
reconstructed from unrelated samples. Health identity/malformed/stale/disconnected
gaps prevent valid-source deltas. Counter resets, connection-epoch changes and
nonincreasing health timestamps prevent misleading differences. Latency percentiles
are cumulative worker-health percentiles, **not** phase-local distributions.
Descriptive known-interval comparisons cannot prove continuous streaming between
samples or acceptable interference. No numerical interference threshold is added;
setup acceptance remains authoritative. No phase with critical gaps is certified.

The new `verify_evidence()` performs offline schema/count/footprint and exact file
checksum checks. Output directories are no-clobber. Final files are atomically
renamed; `SHA256SUMS` is published last and the directory is fsynced. Handled read,
capacity, storage, sample or clock failures and Ctrl-C produce `INCOMPLETE` summaries
where finalization is possible. Unexpected termination leaves `.incomplete` or no
manifest; integrity failure blocks completion. A checksum-valid INCOMPLETE bundle
is still incomplete. Do not treat existence of `summary.json` alone as completion.
Never rerun into the same directory or mutate original evidence.

## Offline example and verification

56 focused tests passed with socket connections and DNS blocked, including CLI
subprocesses. Coverage includes valid/abnormal health, missing/stale/timestamp/schema
failures, identity mismatches, resets and negative counters, all missing phases,
process isolation and filesystem misattribution, no-clobber, sample/storage/capacity
limits, authorization rejection, secret suppression, interruption, manifests,
determinism, read-only adapter bounds and production-routing isolation.

Synthetic example is committed in `docs/reports/alpha_atlas_v4_stage_b_monitor_synthetic.v1/`.
It contains 12 samples over a simulated two-minute period, all three phases, an
absent health observation, stale health, an apparent restart/reset and disconnect.
Its healthy baseline demonstrates descriptive throughput; abnormal phases remain
UNKNOWN. These are invented **synthetic test values**, never actual worker evidence.

Run an offline example locally, into a fresh path outside `/var/data`:

```bash
python -m scripts.run_alpha_atlas_v4_stage_b_monitor \
  --offline-synthetic --output-dir work/monitor-example-unique
```

Validate an existing evidence bundle without network:

```bash
python -c 'from pathlib import Path; from moneybot.services.alpha_atlas_v4_stage_b_monitor import verify_evidence; print(verify_evidence(Path("work/monitor-example-unique"))["status"])'
```

## Future owner authorization and manual runbook — NOT EXECUTED

No live approval artifact is created by this task. Presence of REDIS_URL or a prior
acquisition authorization cannot authorize monitoring. The owner must separately
review/pin a **distinct** `alpha-atlas-v4-stage-b-monitor-authorization.v1` document
binding the exact deployed Git revision, target worker, purpose, UTC interval,
duration, sampling/sample/storage caps, phases, output, read scope, resource/source
bindings and stop conditions. Code installation on the approved worker revision,
Redis read permissions and explicit connection configuration, a verified persistent
mount/device, and optional accessible target PID binding are future prerequisites.
No deployment or secret/environment changes occur here.

The following is a structural example **not an authorization**. Replace placeholders,
review source/mount/process attribution and approve explicitly. All listed fields
are required; extra fields are rejected. A null PID means unavailable process
measurements. The production payload currently lacks worker-instance identity, so
`worker_instance_id=null` is honest and produces UNKNOWN identity.

```json
{
  "schema_version": "alpha-atlas-v4-stage-b-monitor-authorization.v1",
  "status": "APPROVED",
  "owner_approval": "APPROVED_FOR_BOUNDED_PASSIVE_MONITORING",
  "authorization_id": "owner-approved-unique-id",
  "purpose": "PASSIVE_STREAM_RESOURCE_EVIDENCE_ONLY",
  "source_revision": "<exact-40-character-deployed-git-sha>",
  "target_worker": "moneybot-market-stream",
  "starts_at": "<approved-timezone-aware-UTC-start>",
  "ends_at": "<approved-timezone-aware-UTC-end>",
  "max_duration_seconds": 120,
  "sampling_interval_seconds": 10,
  "max_samples": 12,
  "storage_limit_bytes": 2097152,
  "output_dir": "/var/data/moneybot-stage-b-monitoring/<unique-id>",
  "redis_read_scope": {"commands": ["GET"], "key": "moneybot:market:v1:health"},
  "source_binding": {"kind": "OWNER_BOUND_HEALTH_KEY", "worker_instance_id": null},
  "resources": {"pid": null, "start_ticks": null, "boot_id": null, "filesystem_mount": "/var/data", "filesystem_device": "<verified-integer-st_dev>"},
  "phases": [
    {"phase": "BASELINE_BEFORE", "starts_at": "<phase-UTC-start>", "ends_at": "<phase-UTC-end>", "workload_marker_sha256": null},
    {"phase": "DURING_AUTHORIZED_TEST", "starts_at": "<phase-UTC-start>", "ends_at": "<phase-UTC-end>", "workload_marker_sha256": null},
    {"phase": "AFTER_TEST", "starts_at": "<phase-UTC-start>", "ends_at": "<phase-UTC-end>", "workload_marker_sha256": null}
  ],
  "stop_conditions": ["AUTHORIZATION_EXPIRATION", "CAPACITY_EXHAUSTION", "INTEGRITY_FAILURE", "SAMPLE_LIMIT", "READ_ERROR"],
  "content_sha256": "<canonical-body-hash-excluding-this-field>"
}
```

The internal content hash is SHA256 of `canonical_bytes(body)` without
`content_sha256`. The independently pinned CLI hash is SHA256 of the complete
canonical document including that field. Approval must precede invocation; neither
hash grants approval by itself. Phase times must be ordered, nonoverlapping and
inside the observation window. Owner supplies any independently preserved workload
markers separately; monitoring approval never approves the workload.

After separate owner approval only, on the exact approved checkout during its
window, future syntax is:

```bash
python -m scripts.run_alpha_atlas_v4_stage_b_monitor \
  --passive-observation \
  --authorization /var/data/monitor-authorizations/<approved-monitor.json> \
  --authorization-sha256 <independently-pinned-complete-canonical-sha256> \
  --output-dir /var/data/moneybot-stage-b-monitoring/<unique-id>
```

The command is inactive without an explicit mode and rejects absent, wrong-scope,
expired, mismatched-revision, locally edited-source or hash-invalid approval before Redis construction.
It stops at its bound window/caps; Ctrl-C records INCOMPLETE when safe finalization
is possible. Inspect the bundle and its checksums without retries or automatic
restarts. Do not create background scheduling, startup hooks or a permanent observer.

This implementation cannot recover October 7 telemetry, prove worker identity or
mount sharing, or certify prospective Stage B acceptance. Future passive collection
and any workload need separate explicit owner authorization. No live Redis,
Massive/AWS, deployment, acquisition, continuation, replay, pilot, training, fitting,
scoring, trading, promotion or Stage C execution occurred. Stop after publishing
this implementation PR.

Offline consistency checks also verified preservation of every previously checked
checklist item and original stream/Stage B code and closure reports. Original
normalization repair, setup/manifest and owner-bound runtime-config checksum
manifests passed; synthetic output checksums/schema/count/footprint passed.
`git diff --check` passed. No unrelated test suite or operational test was run.


## Redis URL safety repair after PR #659

The [P1 review finding](https://github.com/Moneybot-lab/Moneybot/pull/659#discussion_r4225198551)
identified redis-py's `from_url` precedence: URL query parameters override keyword
settings. The repaired adapter never forwards an untrusted URL to `from_url`.
It rejects unsafe/conflicting options before construction, builds an explicit
validated pool with fixed two-second timeouts, zero retries/health checks, RESP2,
no client-identification commands and one connection, and explicitly closes the
owned pool. Authentication, database selection, verified TLS custom CAs and paired
client certificates remain supported without widening health-key GET scope.

110 focused monitor tests pass with actual socket connections and DNS blocked,
including subprocesses. Regressions reproduce URL precedence with real redis-py
6.4.0 pool parsing without connecting, reject malicious/encoded/duplicate options
before construction, inspect effective real pool settings, exercise zero retries
with redis-py connection errors and check the real handshake's AUTH/SELECT-only
command sequence using in-memory stubs. Original synthetic evidence remains
immutable evidence of the earlier implementation; its recorded module hash is
historical, not the fixed module's hash. Fresh synthetic evidence was generated
only in scratch storage and integrity-checked. This repair does not authorize any
live observation, deployment or workload, or change UNKNOWN acceptance/Stage B OPEN.
