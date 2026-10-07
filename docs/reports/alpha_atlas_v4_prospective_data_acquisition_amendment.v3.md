# Alpha Atlas V4 prospective data-acquisition amendment v3

**Status: `ACCOUNT_EVIDENCE_INCORPORATED_PREPARED_FOR_REVIEW_NOT_AUTHORIZED`.** This preserves v1 and v2 as published evidence. It retains v2 dependency, revision, lookback, request, storage, deadline, and authorization accounting while incorporating user-supplied authenticated Massive and Render dashboard evidence.

## Recommendation and decision

Use a separate single-concurrency Massive REST acquisition stage on the existing
Render worker, a proposed **2 GiB primary persistent disk**, and a distinct
versioned **1 GiB backup destination** with checksum read-back and a 180-day
lifecycle. The snapshot assembler remains cache-only and sees only validated
handoff manifests.

This plan is **not ready for approval**. The active provider plan and existing compute are now evidenced, but there is no supported writable/versioned backup destination, approved stock/sector/identity manifest, worker disk/root, retained-data terms, or operational restore evidence. Nothing here authorizes implementation, live verification, collection, purchase, provisioning, or deployment.

## Newly verified account and runtime evidence

User-supplied authenticated dashboard screenshots, reviewed 2026-09-29, show:

* Active **Massive Stocks Advanced**, Individual, displayed at **$200/month**.
* Existing `moneybot-market-stream` Render **Starter** worker at **$7/month**,
  0.5 CPU and 512 MiB RAM, autoscaling off, with **no disk attached**.
* Existing Moneybot web **Standard** service at **$25/month**, 1 CPU and 2 GiB
  RAM, with an existing **1 GB disk**. Its graph suggests roughly 0.15 GB used,
  but a graph is not a free-byte or quota measurement. Scaling is unavailable
  while its disk is attached.
* Render workspace **Pro**, with one visible member in the Admin role.

These screenshots are not repository files, so no byte hash is claimed. They do
not show the disk mount path, `MONEYBOT_PERSISTENT_DATA_DIR`, backup lifecycle,
service credentials, billing invoice, retained-data terms, request/fair-use
controls, remaining capacity, or restore evidence.

The existing web disk does not satisfy the recommendation: Render disks are
single-service runtime storage, it belongs to the web service rather than the
worker, and 1 GB is below the corrected 2 GiB primary operating requirement. No
production data or runtime was touched to inspect it.

## Preserved v2 correction record

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

* **Provider:** Stocks Advanced Individual is now observed at **$200/month**.
  It is an existing subscription, so the conditional incremental subscription-
  upgrade estimate is **$0**—not a claim that every request or retention use is
  authorized. The screenshots do not show request/fair-use controls, remaining
  capacity, overage behavior, or 180-day retained-data rights.
* **Existing compute:** worker Starter **$7/month** and web Standard
  **$25/month** are observed sunk charges. Incremental compute is conditionally
  **$0** only if stage B demonstrates adequate 0.5 CPU/512 MiB worker headroom.
* **Primary:** the worker currently has no disk. A proposed 2 GiB worker disk at
  Render's published $0.25/GB-month is **$0.50/month or $3.00 for six months**,
  before tax/minimum conventions. No disk was added.
* **Existing web disk:** 1 GB is observed, but it is inaccessible to the worker
  and below the 2 GiB operating requirement. Its mount/root binding and exact
  free bytes are unknown.
* **Backup:** **UNKNOWN**. No supported writable versioned destination is
  configured, so storage, operations, retrieval, restore, and egress costs remain
  unknown.
* **Known incremental floor:** **$0.50/month** for the proposed worker disk.
  Total incremental cost remains unknown until backup and any compute/egress/
  operation charges are known. Affordability is not established.

Official pages reviewed 2026-09-29 remain [Massive aggregates](https://massive.com/docs/rest/stocks/aggregates/custom-bars),
[Massive splits](https://massive.com/docs/rest/stocks/corporate-actions/splits),
[Massive ticker details](https://massive.com/docs/rest/stocks/tickers/ticker-overview),
[Massive pricing](https://massive.com/pricing), [Render disks](https://render.com/docs/disks),
and [Render pricing](https://render.com/pricing). Public limits/rates and dashboard
plan labels do not replace account terms or remaining-capacity evidence.

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

1. Whether the observed Stocks Advanced terms permit the 3,789 attempts and 180-day retained use, including request/fair-use controls, remaining capacity and overage behavior.
2. Approved signed/hash-verified stock universe and effective-dated sector mapping
   with typed identities.
3. A writable versioned backup destination, 180-day lifecycle, quota, operation/
   retrieval/egress rates, and restore credentials.
4. The worker disk mount path and `MONEYBOT_PERSISTENT_DATA_DIR`, plus whether the existing 0.5 CPU/512 MiB worker can attach 2 GiB and meet 07:30 without an upgrade.
5. Read/write/restore roles and an isolated stage-B restore location.

The pilot measures capture reliability and cohort formation only. It establishes
neither representativeness, broad coverage, predictive advantage, nor readiness to
train. Readiness remains `COMPLETE — BLOCKED_STORAGE_AND_CACHE`; collection stays
disabled and unauthorized; completed historical, timing, feasibility, regression,
and scoring findings remain unchanged.
