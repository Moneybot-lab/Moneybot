# Alpha Atlas V4 prediction/execution timestamp contract

**Schema version:** `alpha-atlas-v4-prediction-execution-contract.v1`
**Status:** research and shadow-data contract; no production routing
**Time standard:** stored and compared as timezone-aware UTC

This contract is the point-in-time boundary for future Alpha Atlas V4 datasets,
evaluation, shadow serving, and serving. It does not certify the current builders as
leakage-safe and does not modify Alpha Atlas V3/V3.1, Track B gates, recommendations,
promotion, or routing.

## 1. Timestamp invariant and execution policy

Every accepted row records:

* `decision_at`: the immutable instant the recommendation was produced.
* `feature_cutoff_at`: the latest instant at which an input could have become known
  to the decision process. Ingestion/receipt constraints may make it earlier than
  `decision_at`; it may never be later.
* `entry_at`: the next eligible official regular-session open strictly after
  `decision_at`. A decision at the exact open is not assumed executable at that
  opening print and therefore enters at the following eligible session.
* `label_start_at`: the executable price timestamp from which the target is measured.
  The initial daily-swing policy requires `entry_at == label_start_at`.
* `exit_at`: the official close timestamp of the target exit session.

The mandatory invariants are:

```text
feature_cutoff_at <= decision_at < label_start_at
decision_at < entry_at <= label_start_at < exit_at
entry_at == label_start_at                 # initial daily-swing policy
```

V4 initially models daily swing recommendations, not intraday trading. Session
calculations MUST use the official U.S. equity calendar applicable to the instrument
and `America/New_York` exchange hours, including holidays, extraordinary closures,
and announced early closes. Results are converted to UTC for persistence/comparison.

The **five-session target** enters at the open of entry session `S0` and exits at the
official close of `S4`: the entry session counts as session 1 and the fourth eligible
regular trading session after it counts as session 5. The **ten-session target** uses
the same rule and exits at the close of `S9`. A session counts only if the official
calendar schedules a regular session and a valid exit price can be established.
These are open-to-close targets, not legacy close-to-close `idx + N` targets.

The research exit is the unadjusted official close of the applicable exit session,
paired with explicit point-in-time corporate-action adjustments. Missing data,
halts/delistings, or corporate actions invoke the exceptions below; the builder must
not silently choose another date or price. Each execution record reserves
`transaction_cost_bps`, `entry_slippage_bps`, and `exit_slippage_bps`. Values may be
null until a later policy version implements them, but the fields and price sources
must be present.

## 2. Universal feature availability rule

Daily OHLCV is permitted only for a fully completed official session whose close was
at or before `feature_cutoff_at` and whose provider availability can be proven. A
premarket or regular-session decision cannot use the current session's final close,
high, low, VWAP derived from full-day aggregates, or full-day volume. Current-session
data is allowed only from timestamped intraday bars with a complete bar-close time at
or before `feature_cutoff_at`; partial bars are forbidden.

The identical cutoff applies independently to the traded symbol, SPY, sector ETFs,
volatility proxies, breadth constituents/aggregates, and every market-regime input.
Each feature family records its actual latest source-bar timestamp. Provider date
labels alone do not prove availability. Joins, rolling windows, adjustments, cached
values, and derived features inherit the maximum timestamp of all inputs and may not
cross the cutoff. When availability cannot be proven, reject the row—do not backfill,
forward-fill, median-fill, infer a close time, or substitute another benchmark.

## 3. Session and security-state rules

For all states, `entry_at` is the first eligible regular-session open strictly after
`decision_at`, subject to the instrument being listed and tradable. “Latest daily”
means latest provider-available, fully completed session at/before the cutoff.

| Decision/security state | `feature_cutoff_at` | Latest daily bar | Timestamp-safe intraday | Entry and fail-closed rule |
|---|---|---|---|---|
| Premarket | Latest proven data-availability instant no later than `decision_at`; normally the decision snapshot/receipt cutoff. | Prior completed session only. | Yes, only completed premarket bars when the provider identifies extended-hours bars and close timestamps. | Same-day regular open if strictly later and symbol is eligible. Reject unknown timestamps, stale-required inputs, or no valid opening price. |
| Regular session | Latest proven snapshot/bar-close instant no later than `decision_at`. | Prior completed session only, even if a provider already exposes a provisional current-day aggregate. | Yes, completed bars only; never the forming bar. | Next eligible session's open (not the current session). Reject if any required family relies on current daily OHLCV or an unprovable cache. |
| After hours | Latest proven instant no later than `decision_at`. | Current session is allowed only after its official close and proven provider availability; otherwise prior session. | Yes, completed regular/after-hours bars at/before cutoff. | Next eligible session open. Reject uncertain finalization, timestamps, or required stale data. |
| Weekend/holiday | Latest proven instant no later than `decision_at`. | Most recent completed eligible session. | Only genuinely timestamped extended-hours bars from a valid trading venue; never fabricated calendar bars. | Next official eligible open. Reject if the calendar/version or last-session completion cannot be established. |
| Early close | Latest proven instant no later than `decision_at`, using the announced close. | Current daily bar only after the official early close and proven availability. | Completed bars through cutoff are allowed. | Next eligible open. Reject if ordinary 16:00 New York close was assumed or early-close metadata is absent. |
| Halted security | Same general cutoff; halt status/announcement must itself be time-stamped. | Only a completed pre-halt session/bar; never synthesize a halt-period close. | Completed pre-halt bars allowed if timestamps and status are proven. | Use the next eligible open only if an executable official open exists after resumption. Reject rather than carry forward, guess, or substitute a quote. |
| Missing/stale bars | Latest instant for which provider availability and staleness are established. | Only a complete, non-stale bar satisfying the feature family's declared tolerance. | Only complete, non-stale bars. | Reject if a required family is missing/stale or staleness cannot be measured; no imputation can make a row timing-valid. |
| Newly listed | Normal cutoff, plus verified listing effective time. | Completed post-listing sessions only. | Post-listing completed bars only. | Enter only after decision and listing when a real official open exists. Reject if required lookbacks are incomplete; do not pad pre-listing history. |
| Delisted | Normal cutoff, plus time-stamped delisting/status data. | Completed pre-delisting sessions only. | Pre-delisting completed bars only. | Reject if the planned entry is not executable. If delisting occurs after entry but before exit, preserve the row only under a separately versioned terminal-value policy; until then reject, never use the last close as the exit. |
| Split, stock dividend, ticker change | Normal cutoff; action data must have a provider publication/effective timestamp at/before the point where used. | Same completed-session rule, with explicit adjustment IDs and point-in-time identity. | Allowed under the same cutoff and identity mapping. | Entry/exit prices retain their documented raw sources and are normalized only by the declared action policy. Reject unknown factors, effective times, identity continuity, or mappings; never apply an action learned after the relevant as-of point without recording that research adjustment. |

## 4. Required row and execution provenance

Every future training or shadow row must be capable of recording, without relying on
an external mutable lookup:

* contract/schema version and model/feature-contract version;
* decision ID, displayed symbol, immutable point-in-time symbol identity, exchange,
  and trading-calendar identifier/version;
* `decision_at`, `feature_cutoff_at`, `entry_at`, `label_start_at`, and `exit_at`;
* latest permitted source-bar timestamp **for every feature family**, including
  symbol daily/intraday, SPY, sector, volatility, breadth, and regime families used;
* entry- and exit-price source (for example official open/close and provider field);
* provider/source identifier and corporate-action adjustment identifiers;
* staleness status and a rejection reason for an unusable attempted row;
* code commit and dataset-manifest content hash; and
* transaction-cost, entry-slippage, and exit-slippage fields, including explicit
  nulls while assumptions remain unimplemented.

An accepted row has `rejection_reason = null`. Rejected attempts should be retained
in rejection/audit output with a stable reason, but must not enter training or metric
denominators. The typed representation in
`moneybot.services.alpha_atlas_v4_timing_contract` validates accepted record
provenance and ordering. Dataset builders remain responsible for official-calendar
calculation and for emitting rejected-attempt audit records.

## 5. Known legacy conflicts (not resolved here)

The current Track B Massive builder reduces the decision Unix timestamp to a UTC
date, selects the bar on or before that date, uses that close and full-day fields as
features, and labels from that selected close to `idx + horizon`. Thus a decision
before the close can include that day's completed aggregate, and its label does not
use the next executable open. The V3/V3.1 serving dry runs also request daily history
without a timestamp cutoff. Existing endpoint outcome code similarly downloads daily
history starting at the event date and indexes closes. Those behaviors are frozen for
this task and are not definitions of V4 correctness.

V3/V3.1 artifacts and their “leakage safe” metadata remain benchmark evidence only;
this V4 contract does not retroactively certify those datasets or artifacts. No V4
production routing or automatic promotion is authorized by this document.

## 6. Versioning and change control

Changing session counting, entry/exit price selection, cutoff calculation, allowed
bar type, calendar, corporate-action handling, or cost semantics requires a new
contract version and dataset manifest. Implementations must compare the exact version
constant, fail closed on unknown versions, and keep V4 imports isolated until a
separate migration is reviewed and leakage-tested.
