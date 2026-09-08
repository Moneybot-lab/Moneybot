# Alpha Atlas V4 prior-state feature classification

**Feature contract:** `alpha-atlas-v4-features.v2`  
**Canonicalization contract:** `alpha-atlas-v4-canonical-observation.v2`  
**Canonical schema:** `alpha-atlas-v4-canonical-observations.v2`

## Confirmed lineage and semantics

The raw builder groups the exported decision log by current ticker symbol, orders all
requests by normalized event timestamp, and selects the immediately preceding request
strictly before the current timestamp. The lookup is symbol-global: it is not restricted
by endpoint, user, watchlist, portfolio, prior model, or product surface. Counts use the
same symbol-global request history over seven calendar days. The probability comes from
the prior event's snapshot/payload and therefore is a prior model output, not market data.
The current probability is also an event snapshot/payload value. A missing prior event or
probability yields null for the delta; recommendation flags default to zero.

Consequently simultaneous requests can see different values solely because request order
or unrelated endpoint traffic changed. The prior model artifact is not required to be
frozen or identified, historical reconstruction depends on the exported request stream,
and no live train/serve parity contract exists. These fields were legacy Track B
model-echo diagnostics; they were never approved as V4 point-in-time market features.
They create prior-model circularity and can vary while all market features, cutoff,
execution, and outcome remain identical.

## Complete prior-state family inventory

| Field (legacy `feature_` prefix omitted) | Current source | Point-in-time source | Model feature before v2? | Canonical economic field? | Request metadata? | V4 disposition |
|---|---|---|---:|---:|---:|---|
| `probability_up_delta_from_last_signal` | Current minus previous exported model probability | Prior request snapshot/payload | Yes, but baseline denylisted it as model echo | No | Yes | Deprecated from V4 model; preserve in request map |
| `previous_recommendation_buy` | Previous symbol request recommendation | Prior request payload/snapshot | Yes, model echo | No | Yes | Request-level audit metadata |
| `recommendation_changed` | Current versus previous symbol request recommendation | Two request records | Yes, model echo | No | Yes | Request-level audit metadata |
| `days_since_last_signal` | Current minus previous symbol request timestamp | Request log ordering | Yes, model echo | No | Yes | Request-level audit metadata |
| `symbol_signal_count_7d` | Count of symbol requests in trailing seven calendar days | Request log | Yes, model echo | No | Yes | Request-level audit metadata |
| `symbol_buy_count_7d` | BUY/STRONG BUY request count | Request log recommendations | Yes, model echo | No | Yes | Request-level audit metadata |
| `symbol_sell_count_7d` | SELL request count | Request log recommendations | Yes, model echo | No | Yes | Request-level audit metadata |
| `prior_signal_at` | Previous symbol request timestamp | Request log | No | No | Yes | Audit timestamp in request map only |
| `prior_signal_source_identifier` | Prior model version when present, otherwise decision source | Request snapshot/payload | No | No | Yes | Audit source in request map only; not parity proof |
| Endpoint/user/watchlist/portfolio/alert state | Product request context | Request record | No | No | Yes | Existing request-map audit metadata; private identifiers excluded from diagnostics |
| Provider event sequence numbers | Market stream transport ordering | Provider event | No in daily V4 | Blocked pending intraday contract | No | Not part of this request-history family |

Market OHLCV, SPY/sector/regime inputs, timing, execution prices, targets, and
corporate-action provenance remain canonical material data and continue to fail closed
on disagreement.

## Hosted artifact limitation

The hosted bundle is not accessible in this checkout because no Git remote or GitHub
credentials are configured. Therefore hosted raw-row/group counts and distributions
cannot be truthfully reported here. The improved failure diagnostic now reports all
bounded material conflicts, group/row counts, null-versus-value conflicts, numeric
ranges, classifications, and corrective owners before the command exits nonzero. It
never includes request or user identifiers.

The deterministic adversarial fixture contains two endpoint requests with identical
market economics and different prior-signal deltas. Under V1 it produced one proposed
group containing two rows and failed before emitting an observation. Under V2 it
produces one compatible canonical observation, two request-map rows, one collapsed
duplicate, and total model sample weight one. These are synthetic regression counts,
not hosted or production counts.
