# Alpha Atlas V4 Stage B provider normalization repair v1

## Root cause

The persisted Massive stock aggregate envelopes contain raw rows keyed by `o`, `h`, `l`, `c`, `v`, `vw`, optional `n`, and `t`. The Stage B consumers instead looked for canonical `date`, `open`, `high`, `low`, `close`, `volume`, and `vwap` fields directly on those immutable raw rows. Feature-window validation therefore received empty dates; adjustment would have lacked its required fields; handoff eligibility independently repeated the raw-date assumption.

Massive's official Custom Bars documentation, retrieved October 8, 2026, documents `t` as the Unix-millisecond start of the aggregate window, the abbreviated value fields, the response-level `adjusted` flag, and the ticker/status/count/request envelope. Retrieved documentation bytes were 1,630,905 bytes with SHA-256 `8a5bd30b98a4f0421a09cfa09f99ed35c37832a2d596b33e9107295671899308`; the large dynamic page is not committed.

## Repair

One strict shared adapter now validates the envelope and receipts, converts millisecond aggregate starts through `America/New_York` and the registered XNYS calendar, rejects invalid units/anchors/non-sessions/out-of-window rows/duplicates/nonfinite or inconsistent values, and emits canonical derived rows without changing raw payloads. Provenance binds every source page and receipt, actual receipt time, row index and provider timestamp, adapter/calendar/basis versions, split lineage, adjustment engine, and derived hashes.

Feature warm-up, pairwise alignment, split adjustment for AAPL/SPY/XLK, and handoff eligibility consume the same normalized objects. Object-shaped identity results remain objects in raw evidence but are handled correctly by the identity gate. Empty complete split results remain valid; incomplete pagination remains blocking.

## Exact saved-evidence replay

`/var/data/moneybot-stage-b` is not accessible in this development checkout. Exact replay and backup/restore inspection are therefore **pending**, not fabricated. The offline replay validates all five response bytes/receipts, five-attempt Massive ledger, 91-operation S3 ledger, normalized histories, identity, split lineage, handoff, backup inventory/completion receipts, and available isolated-restore bytes. It makes no network calls and does not alter original evidence or the failed live result.

```bash
python -m scripts.replay_alpha_atlas_v4_stage_b_saved_evidence \
  --saved-evidence-root /var/data/moneybot-stage-b \
  --output-dir /var/data/moneybot-stage-b/replay/alpha-atlas-v4-stage-b-normalization-repair.v1
```

Expected fresh outputs are `report.json`, `report.md`, `run.log`, and relative-path `SHA256SUMS`. A replay PASS is only a derived repair result; it does not convert the original live execution to PASS or establish premarket/prospective/pilot readiness.
