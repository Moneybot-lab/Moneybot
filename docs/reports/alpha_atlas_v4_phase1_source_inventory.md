# Alpha Atlas V4 Phase 1 technical source inventory

**Schema:** `alpha-atlas-v4-phase1-source-inventory.v1`  
**Scope:** private personal-use investment research only  
**Verdict:** full-universe backfill is technically blocked; no backfill was run.

The authoritative machine-readable inventory is
[`alpha_atlas_v4_phase1_source_inventory.json`](alpha_atlas_v4_phase1_source_inventory.json).
It records provider/dataset, configured location, observed date bounds, adjustment and
timestamp semantics, point-in-time capability, inactive-security status, gaps,
feature dependencies, readiness, and blockers for every required source.

| Source | Required use | Technical finding |
| --- | --- | --- |
| Raw daily OHLCV/VWAP | All 43 inputs and executable labels | Individual bounded probes required; common date range unverified |
| Active/inactive security reference | Survivorship-safe universe | Delisted inclusion and permanent identity unverified; full universe blocked |
| Ticker/corporate events | Ticker changes, mergers, acquisitions, bankruptcies, listings | Effective-dated event chain unverified |
| Splits | Feature and holding-period economic adjustment | Existing logic is PIT-aware; historical source completeness remains to be probed |
| Dividends | Not used by current price-return V4 contract | Not required unless a future version explicitly adopts total returns |
| SPY daily bars | Market returns, beta, regime, volatility | Same raw/session contract as symbol bars |
| Sector ETF bars and mapping | Sector-relative return | Bars plus effective-dated sector mapping required; current-state sector is forbidden |
| XNYS calendar | Sessions, holidays, early closes | Repository rule contract available; not represented as an authoritative exchange feed |
| Terminal-price policy | Delisting/acquisition/bankruptcy outcomes | Missing exits reject; an invented terminal price is forbidden |

The optional preflight is read-only, capped at 16 representative requests and
1 MiB per response, and never emits credentials. Dry-run is the default. Its cases
cover active, delisted, ticker-change, split, new-listing, SPY, sector ETF, regular
session, early close, and older history. A successful individual probe proves only
that case; it does not prove universe completeness.

The technically valid reduced-scope alternative is an explicitly named,
contemporaneously tradable cohort whose identities and event histories can be
proved. It must be labelled as cohort research and must not be presented as a
survivorship-safe market-wide backfill.
