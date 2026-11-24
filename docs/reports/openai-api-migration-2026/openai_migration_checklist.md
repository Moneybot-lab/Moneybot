# OpenAI migration closure checklist

November 24, 2026. Checked boxes describe audit work. Empty boxes describe evidence or work still due. Audit **COMPLETE_WITH_LIMITATIONS**. Migration readiness **ADDITIONAL_EVIDENCE_REQUIRED**.

## Audit record

- [x] Match repository main against GitHub, create the dedicated audit branch and inventory the two provider workloads, UI callers, operational triggers and non-production references.
- [x] Verify the six December 11 alias/snapshot families, official replacements, Nov 30 prompt and service deadlines, model cards, supported effort and prices.
- [x] Trace factory/constructor and available deployment configuration, failure handling, old production trace, expected per-view fan-out, authority, general research lineage and protected evidence.
- [x] Separate historical production evidence, hypothetical cost scenarios, untested candidates and unknown runtime configuration.
- [x] Record audit-only deliverables, proposal byte hash, baseline and verification method.
- [ ] Confirm current production model bindings and nonsecret project-level usage. Reclassify A01 to P0 for each confirmed active retired binding.

## Owner verification

Perform these steps in read-only mode. Keep secrets masked. Do not run `env`, export environment groups, copy a connection string, query customer rows, invoke Quick Ask for a status check, click “Save”, or trigger a redeploy. The useful evidence fits in one row per service and workload.

1. In **Render Dashboard → workspace → project/environment**, identify the production web service linked to `Moneybot-lab/Moneybot`, the market-stream worker and any cron/one-off consumer. In **Settings → Build & Deploy** record service type, linked branch, root, start command classification and preview policy. In **Deploys** record the actual live successful commit SHA and time. If the SHA differs from the audited base, diff that SHA's application, environment resolution and requests before declaring findings current.
2. On each applicable service's **Environment** page inspect the *names* of the service-specific entries and linked groups. For model selection only, record the individual nonsecret `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`, `AI_TIMEOUT_SECONDS`, `AI_FAILURE_COOLDOWN_SECONDS` and `AI_RESPONSE_CACHE_TTL_SECONDS`. Record “absent” explicitly. For `AI_API_KEY` have the owner attest “present/nonblank”, “absent” or “unknown” without revealing its value. Do not expand secret files. Check the start command for an explicit assignment overriding service environment and inspect only allowlisted settings if applicable.
3. Open each linked **Environment Group** by name. Compare those same allowlisted model settings, note collisions and whether a service-specific value supersedes a group. Multiple groups defining the same key need reconciliation. Confirm the last applied deploy occurred after any saved-only setting change. Resolve the model exactly as `app_factory.py:482` and `ai_advisor.py:117` do. Never mark AI active solely because a key exists.
4. Review worker and cron commands, external schedulers and admin/load-test clients for calls to `/api/quick-ask`, `/api/user-watchlist`, notebook/SDK clients or `/v1/prompts`. Keep credentials and token-bearing URLs out of the evidence. If remote prompts/Agent Builder/Evals workflows exist, record owner and use before Nov 30, and complete any authorized prompt migration/transition by Nov 29. Compare actual schedules with the notification schedule and GitHub job definitions without running them.
5. In **GitHub Settings → Secrets and variables → Actions** and each applicable **Environment**, review repository, environment and inherited organization **names**, then cross-reference workflow expressions and reusable workflow calls. For model variables only, obtain a redacted name/ID/binding attestation. No secret values are needed. The connector's repository secret-metadata request returned 403. A secret name alone does not prove that a job consumes it.
6. Review persisted application settings with the owner against the deployed ORM/constructor and any external configuration system. Confirm if an out-of-tree deployment injects a model at startup. No production DB query is needed for the audited path. Review nonsecret model-project entitlement, allowed regions/service tier and RPM/TPM on the OpenAI project Limits page. In Usage, filter to the relevant project/time period (7–30 days) and share only aggregate model/day, volume, input/cached/output and billed cost. Existing safe counters may identify retries and skips. No prompt or account-level content needed.
7. Record evidence as **service type | environment | deployed SHA/time | workload | enabled | credential present (boolean/unknown) | provider | resolved model ID | source/precedence | last observed date | requests/tokens/cost aggregate | approver**. Unavailable or ambiguous fields stay unknown. Reconcile provider model counts with routing because both workloads may currently share one ID.

## Release gates

- [ ] **Nov 24–26:** release owner resolves A01 and documents external systems before the first deadline. Quant owner identifies unlocked-portfolio exposure (A03/A05). Approve the exact proposal hash and the permitted next phase in writing.
- [ ] **A / Nov 27–30:** land only separately approved model/effort and authority repairs on an implementation branch. Resolve A02–A04 for affected paths, including safe missing-authority behavior in API and UI. Stage fixes separately from optimization.
- [ ] **B:** validate per-workload profiles and IDs offline; retain known-good supported fallback tuples, nonsecret diagnostics, bounded retries, cost caps and lane-level disable. Verify cache invalidation at cutover (A06/A07).
- [ ] **C / Dec 1–3:** pass T01–T10, attach fixture versions, aggregate results, unchanged baseline/protected hashes, rollback drill and independent quantitative approval. Only if separately authorized run the exact eight-request probe within $0.02.
- [ ] **D / Dec 4 target:** separately authorize preview/canary and production, verify service/group binding and selected SHA, monitor schema-valid rate, first paint, time to final advice, costs/retries and authoritative actions. Record signed go/no-go.
- [ ] **Dec 5–10:** monitor by workload, including deterministic degradation and cost per accepted output. Rehearse the validated, supported rollback pairing with provider access disabled in an isolated environment.
- [ ] **Dec 11 and after:** confirm zero retiring-model routing or retry/fallback references in effective profiles, monitor declines and action provenance, record post-retirement smoke evidence and rollback readiness.
- [ ] **E:** evaluate premium/caching/batch only against a separately approved commercial case. Close only after evidence is reviewed. No merge, deployment, scoring or live testing authorization follows from this checklist.

[Finding register and rollback proposal](openai_migration_implementation_proposal.md) · [Validation plan](openai_migration_validation_plan.md) · [Proposal checksum](implementation_proposal.sha256)
