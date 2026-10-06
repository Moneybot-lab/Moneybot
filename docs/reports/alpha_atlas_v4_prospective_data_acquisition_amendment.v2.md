# Alpha Atlas V4 prospective data-acquisition amendment v2

**Status: `CORRECTED_PREPARED_FOR_REVIEW_NOT_AUTHORIZED`.** This preserves v1
(commit `1c2400d`) as published evidence and corrects its incomplete dependency,
revision, lookback, request, storage, cost, and authorization accounting.

## Recommendation and decision

Use a separate single-concurrency Massive REST acquisition stage on the existing
Render worker, a proposed **2 GiB primary persistent disk**, and a distinct
versioned **1 GiB backup destination** with checksum read-back and a 180-day
lifecycle. The snapshot assembler remains cache-only and sees only validated
handoff manifests.

This plan is **not ready for approval**. There is no supported writable/versioned
backup destination, approved stock/sector/identity manifest, or account-specific
Massive entitlement/cost evidence. Nothing here authorizes implementation, live
verification, collection, purchase, provisioning, or deployment.

## Concise v1 correction record

* Adds the supplied universe and effective-dated sector mapping as explicit inputs.
* Stores `adjusted=false` raw bars and performs exactly one local, lineage-bound
  adjustment instead of mixing changing provider-adjusted windows.
* Replaces “60 sessions provides the history” with a fixed 75-session request and
  explicit valid/pairwise-aligned observation gates; 75 is not a guarantee.
* Adds recurring dated identity checks and full-window refreshes for correction and
  split consistency.
* Adds a separately authorized operational check and one ledger across all stages.
* Revises requests from 743/2,271 to **1,248 expected / 3,789 maximum**.
* Revises capacity from a 512 MiB near-limit estimate to **1,536 MiB retained
  evidence**, **2 GiB primary operating capacity**, and **1 GiB backup capacity**.

## Complete dependency and request accounting

| Dependency | Class | Bootstrap | Each session | Endpoint/rule and failure |
|---|---|---:|---:|---|
| Universe source | **A: supplied, not yet verified** | 0 | 0 | Signed/hash-verified, effective-dated ≤50 common-stock manifest. No repository source exists; absence blocks acquisition |
| Typed identity | **C + D** | 50 expected / 150 max | 50 / 150 | Dated `/v3/reference/tickers/{ticker}`, one page, three attempts. Missing/change/ambiguity blocks ticker |
| Effective-dated sector map | **A: supplied, not yet verified** | 0 | 0 | Must cover all sessions or append effective changes. Current ticker details cannot prove sector history; absence/expiry blocks ticker |
| Stock/SPY/sector history | **C + D full refresh** | 62 / 186 | 62 / 186 | `/v2/aggs/.../1/day/...` with `adjusted=false`; 50 stocks + shared SPY + 11 shared ETFs; one page and three attempts |
| Split lineage | **C + D** | 1 / 15 | 1 / 6 | Global `/stocks/v1/splits`, filtered to 62 symbols; five-page bootstrap and two-page recurring caps, three attempts/page |
| Corrections/revisions | **D, included** | 0 extra | 0 extra | Each recurring history call returns the complete fixed window. Differences append versions; no unbudgeted refresh |

Class **B** remains conditional: an existing authorized Track B flat-file response
may replace the corresponding REST history object only when exact symbols,
window, receipt, license, publication time, and durable transfer verify. It never
causes both paths to run or converts insufficient coverage into “zero requests.”

The expected bootstrap interpretation remains exactly **62 history + 50 identity
+ 1 split page = 113**. Previously omitted inputs are the supplied universe and
sector mapping, recurring identities, correction-consistent full windows, and the
separately counted operational check. They are now explicit rather than hidden.

A `next_url` after the split page cap is `INCOMPLETE_PAGINATION`; reaching a cap
never establishes completeness. It blocks split-dependent eligibility for every
affected symbol.

## Endpoint-level request budget

| Stage | Expected | Hard maximum | Formula |
|---|---:|---:|---|
| A. Implementation/synthetic | 0 | 0 | No network access |
| B. Operational verification | 5 | 18 | Expected `3 history + 1 identity + 1 split`; max `3×3 + 1×3 + 2 split pages×3` |
| Bootstrap | 113 | 351 | Expected `62 + 50 + 1`; max `62×3 + 50×3 + 5 pages×3` |
| Each pilot session | 113 | 342 | Expected `62 + 50 + 1`; max `62×3 + 50×3 + 2 pages×3` |
| Ten sessions | 1,130 | 3,420 | Per-session × 10 |
| **All stages** | **1,248** | **3,789** | Verification + bootstrap + ten sessions |

The proposed finite cap is **3,789 attempts**, not 2,271. Verification, bootstrap,
all pages, retries, failures, and pilot requests use one append-only ledger.
Exact verification objects may later deduplicate, but the cap conservatively
assumes no reuse.

## Adjustment and revision consistency

1. Preserve immutable provider **unadjusted** (`adjusted=false`) OHLCV/VWAP bytes.
2. For each prospective snapshot, select one complete raw response version and all
   split events received by cutoff and effective by that session.
3. Apply canonical split normalization once: transform pre-split open/high/low/close
   **and provider VWAP** by the price factor and volume by its inverse. The current
   pure helper does not adjust VWAP, so stage A must extend it and add parity fixtures
   before any operational check. Never adjust an already adjusted provider value.
4. Bind the result to raw object SHA-256, split-manifest SHA-256, ordered split
   IDs/factors, adjustment-as-of session, and adjustment-engine version.
5. Each session retrieves the complete 75-session raw window, dated identities,
   and incremental splits. Per-date hash or split-factor changes append a new
   version and trigger complete local reconstruction for later snapshots.
6. If a split occurs during the pilot, the new effective basis applies to a newly
   derived complete window. Earlier source objects and snapshots remain immutable;
   no factor is applied twice.

The full-window refresh costs are already the 62 recurring history requests and
storage budget. No extra correction request is permitted. Incomplete split pages,
unknown factors, missing VWAP-adjustment parity, mixed versions/bases, identity mismatch, or reconstruction that
exceeds runtime/storage makes the dependency or ticker ineligible.

Every object retains its actual receipt time. A history retrieval now can support
a permitted future decision; it cannot prove availability at a past decision.

## Corrected observation requirements

The fixed request is exactly the **75 previous eligible XNYS sessions** ending at
the immediately previous completed session. The extra 25 sessions are a bounded
planning tolerance—not a promise of 50 usable bars.

| Computation | Required valid observations |
|---|---|
| SMA50 and price/SMA50 | 50 valid symbol prices |
| 20-session return and 20-return volatility | 21 consecutive valid symbol prices |
| 20-return beta | Same 21 eligible dates for symbol and SPY |
| MACD signal/histogram | 34 valid finite symbol observations |
| Ten-point slope of rolling VWAP20 | 29 consecutive valid OHLCV rows |
| Sector five-return comparison | Same six eligible dates for symbol and its effective sector ETF |

After removing duplicates and rejecting missing/nonfinite fields, insufficient or
misaligned observations produce `INSUFFICIENT_VALID_HISTORY` or
`CONTEXT_ALIGNMENT_FAILED`. The acquisition does not extend the window, replace
the ticker/context, drop a feature, or retry outside budget.

## One deadline

* **07:30 New York:** acquisition is complete and all network activity stops.
* **07:30–07:45:** validation and read-only handoff only; no fetch or retry.
* **07:45:** static universe binding.
* **07:45–08:30:** assembly.
* **08:40 inclusive:** durable readiness.
* **08:45:** frozen decision boundary.

Late/incomplete acquisition remains evidence and produces UNKNOWN, MISSING, or
INVALID as applicable. Every expected ticker remains reconciled. No deadline moves
and no receipt is backdated.

## Reconciled storage and operating capacity

All cap figures below are **uncompressed**. Compression is estimated at 40% only
for planning and never relaxes logical accounting.

| Retained evidence | Expected | Maximum |
|---|---:|---:|
| Operational verification | 0.14 MiB | 3.88 MiB |
| Bootstrap | 1.97 MiB | 55.62 MiB |
| Ten-session acquisition | 19.70 MiB | 533.55 MiB |
| Ten-session snapshots/manifests/mappings | 10.31 MiB | 41.25 MiB |
| Correction/revision reserve | — | 32.00 MiB |
| Operational manifests | 2.00 MiB | 8.00 MiB |
| **Primary retained evidence** | **34.12 MiB** | **674.31 MiB** |
| **Backup retained evidence** | **34.12 MiB** | **674.31 MiB** |
| **Combined retained evidence** | **68.24 MiB** | **1,348.61 MiB** |

Proposed evidence caps are **768 MiB primary + 768 MiB backup = 1,536 MiB**,
leaving 187.39 MiB combined evidence reserve at maximum.

The proposed **2 GiB primary filesystem capacity** separately comprises a 768 MiB
primary evidence cap, 128 MiB atomic staging, 768 MiB restore workspace, and 384
MiB filesystem/operating headroom. The proposed backup capacity is **1 GiB**:
768 MiB evidence plus 256 MiB operating reserve. Restore space is temporary, not
retained evidence. Primary and backup remain through 180 days after session ten.

## Concrete arrangement and conditional cost

* **Primary:** proposed 2 GiB Render disk attached to the existing single-instance
  worker at `/var/data`. Render publicly lists persistent SSDs at **$0.25/GB-month**,
  so arithmetic is **$0.50/month or $3.00 for six months**, before tax, disk
  minimums, or account billing conventions.
* **Backup:** distinct versioned object storage with ≥1 GiB, immutable/versioned
  keys, checksum read-back, 180-day lifecycle, and bounded restore credentials.
  **No supported writable destination is configured**, so its storage, operations,
  retrieval, and egress prices are UNKNOWN and this is a concrete blocker.
* **Provider:** UNKNOWN. Required account fields are plan name, endpoint
  entitlements, request rate, history depth, retention rights, included quota,
  overage policy, and remaining capacity.
* **Compute:** conditionally $0 incremental only if the existing paid worker has
  sufficient measured headroom. Current utilization/billing headroom is UNKNOWN;
  no new service is assumed.
* **Restore/egress:** UNKNOWN. Render publicly lists plan-dependent included
  bandwidth and $0.15/GB overage, but workspace plan, remaining allowance, and
  backup-provider retrieval charges are unavailable.

Public rates are not account entitlement or affordability evidence. Official pages
reviewed 2026-09-29: [Massive aggregates](https://massive.com/docs/rest/stocks/aggregates/custom-bars),
[Massive splits](https://massive.com/docs/rest/stocks/corporate-actions/splits),
[Massive ticker details](https://massive.com/docs/rest/stocks/tickers/ticker-overview),
[Massive pricing](https://massive.com/pricing), [Render disks](https://render.com/docs/disks),
and [Render pricing](https://render.com/pricing).

## Non-circular authorization stages

| Stage | What review may authorize | Explicit boundary |
|---|---|---|
| **A. Implementation and synthetic tests** | Receipt/adjustment/revision logic, finite ledgers, no-fallback handoff and fixtures | Zero live requests and no infrastructure activation |
| **B. Bounded operational verification** | Maximum 18 attempts and 3.88 MiB retained evidence; isolated primary write/read-back, backup copy and restore; observed response size/timing | Separate future authorization; not a bootstrap or pilot |
| **C. Ten-session pilot** | Bootstrap plus ten sessions under the persistent 3,789-attempt ledger | Requires approved static manifests, stage-B evidence, entitlement/cost approval, durable primary+backup and explicit collection authorization |

Implementation does not require measured acquisition performance. Conversely, all
request sizes, runtime, compression and completion timing remain estimates until a
separately authorized stage B observes them.

## Universe, scope, and remaining questions

QQQ/SPY remains `PROPOSED_NOT_APPROVED` and plumbing-only. No replacement roster
is approved. An identifier-sorted supplied common-stock manifest would be
deterministic and outcome-independent, but **would not establish
representativeness**, broad-universe coverage, or meaningful top-five validity.

Exact unresolved inputs are:

1. Massive plan, entitlements, rate, history depth, retention rights, included
   quota, overage policy, and remaining capacity.
2. Approved signed/hash-verified stock universe and effective-dated sector mapping
   with typed identities.
3. A writable versioned backup destination, 180-day lifecycle, quota, operation/
   retrieval/egress rates, and restore credentials.
4. Whether the existing worker can attach a 2 GiB disk and run within compute
   headroom, including the disk minimum/billing convention.
5. Read/write/restore roles and an isolated stage-B restore location.

The pilot measures capture reliability and cohort formation only. It establishes
neither representativeness, broad coverage, predictive advantage, nor readiness to
train. Readiness remains `COMPLETE — BLOCKED_STORAGE_AND_CACHE`; collection stays
disabled and unauthorized; completed historical, timing, feasibility, regression,
and scoring findings remain unchanged.
