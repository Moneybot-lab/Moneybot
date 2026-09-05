# Alpha Atlas V4 Phase 1 historical coverage readiness

**Schema:** `alpha-atlas-v4-phase1-readiness.v1`  
**Verdict:** `BLOCKED_FULL_UNIVERSE_BACKFILL`

This is a private personal-use research project for the owner's investment research
and personal account management. It requires no vendor licensing confirmation,
subscription change, service purchase, or business-plan approval. A future
commercial project is separate, and no commercial consideration appears in this
audit's technical blocker list.

Phase 0 remains certified by Track B `33960970412-1`: 50,219/50,219 export
continuity was prefix-verified, and 32,619/32,619 rows reconstructed against artifact
SHA-256 `2877edece1ad0a8fca48ffff8a313359cb37da37785a87311cb47750ff3196b5`.

The audit implementation is ready, but live technical probes were not executed in
this checkout. Therefore no earliest historical date, delisted inclusion, permanent
identity chain, effective-dated sector history, or terminal-price coverage is
claimed. The full-universe backfill remains blocked. The controlled plan deliberately
has no start date until those sources have a measured common range.

## Authoritative feature mapping

The mapping contains **48 feature-store fields = 43 ordered model inputs + 5
provenance fields**. Training and inference inclusion is identical for the 43 model
inputs. The five additional fields are `feature_cutoff_at`,
`feature_family_available_at`, `feature_family_source_at`,
`feature_market_asof_date`, and `feature_split_ids`; they are never model inputs.

Mapping SHA-256: `80f0fccccf02d7ebcf0f997df02187da00472498fa5618ea4be5588d820770bb`.

## Duplicate handling

The existing canonical contract collapses compatible replay requests, retains request
metadata separately, assigns weight 1.0 per canonical economic observation, and
rejects conflicting immutable identities. The Phase 1 comparison primitive reports
physical/unique/collapsed counts plus class balance, directional accuracy, Brier,
calibration error, positive prediction rate, big-gain capture, and big-loss rate.
Historical decision-log records are not modified.

The `quick_ask` API currently records decisions after recommendation generation;
the audit found no repository-level idempotency key spanning replayed requests.
Because request identity and retry semantics cannot be proven from checkout evidence,
this task does not change the write path. Canonical collapse remains the safe
training boundary; a prospective API repair requires a separately versioned request
idempotency contract and must never rewrite the append-only log.

## Controlled next-prompt plan

The plan downloads effective-dated reference/events first, actions second, raw bars
and context third, then completeness evidence. Partitions are immutable and dated;
temporary downloads are atomically finalized only after hashes pass. Retries are
bounded and resumability trusts only matching manifest entries.

The provisional daily-first scale is 50.4 million rows (5,040 sessions × 10,000
symbols), approximately 1.61 GB compressed, 6.45 GB working, and 19.35 GB with the
conservative backup/headroom multiplier. Request/object count and runtime remain
unknown until the bounded preflight measures the accessible delivery mechanism.

No historical backfill, model training, deployment, promotion, automated trading,
or public/customer routing was performed or authorized.
