# Replacement model matrix
Verified November 24, 2026 using the official API catalog, individual model cards, pricing and migration guides. IDs below are API identifiers. Project entitlement, effective service tier and MoneyBot performance are unmeasured.

## OpenAI lifecycle

| Retiring alias and snapshot | Shutdown | OpenAI's published replacement | MoneyBot source exposure |
|---|---|---|---|
| `gpt-5`, `gpt-5-2025-08-07` | Dec 11, 2026 | `gpt-6.1-sol` | Possible environment override only |
| `gpt-5-mini`, `gpt-5-mini-2025-08-07` | Dec 11, 2026 | `gpt-6.1-terra` | Alias is the Quick Ask and Portfolio default. Snapshot is its documented target. |
| `gpt-5-nano`, `gpt-5-nano-2025-08-07` | Dec 11, 2026 | `gpt-6-luna` | No source binding |
| `gpt-5-pro`, `gpt-5-pro-2025-10-06` | Dec 11, 2026 | `gpt-6.1-sol`, Responses with `reasoning.mode=pro` | No source binding |
| `o3`, `o3-2025-04-16` | Dec 11, 2026 | `gpt-6.1-sol` | No source binding |
| `o3-pro`, `o3-pro-2025-06-10` | Dec 11, 2026 | `gpt-6.1-sol`, Responses with `reasoning.mode=pro` | No source binding |

The deprecation notice specifies 6.1 Sol for the legacy pro workloads. Preserve its explicit pro-mode distinction when reviewing an unexpected override. Today's flagship label alone says nothing about compatibility. [Deprecations](https://developers.openai.com/api/docs/deprecations) · [GPT-5 mini card](https://developers.openai.com/api/docs/models/gpt-5-mini) · [6.1 Sol card](https://developers.openai.com/api/docs/models/gpt-6.1-sol)

Reusable prompt objects and `/v1/prompts` retire Nov 30, as does Agent Builder. Evals dashboard/API/graders for eval workflows enter read-only status Nov 30. App-owned inline prompt content already meets the prompt-object migration pattern. The listed older image models retire Dec 1. Assistants shut down Aug 26, 2026. Jan 6, 2027 brings TTS retirements and fine-tuning creation restrictions. Jan 11 brings specific EU fast/priority restrictions on older GPT-5.x models. Jan 14 brings the listed GPT-4o/mini and 4.1/mini snapshots and Jan 20 affects older realtime/audio IDs. No applicable calls were found in this tree. Confirm external automations before Nov 30. No SDK lifecycle issue applies to these two raw-HTTP call sites. [Lifecycle table and endpoint notices](https://developers.openai.com/api/docs/deprecations) · [Prompt migration guide](https://developers.openai.com/api/docs/guides/prompting/migrate-from-prompt-object) · [Official client libraries](https://developers.openai.com/api/docs/libraries)

## Supported candidates and cost

USD per million tokens on Standard, short-context, input / cached input read / output. Reasoning tokens are included in output. Context/output limits are tokens. No shutdown was listed for the candidate IDs on the verification date; recheck before deployment. A tier's published limits are examples, not an entitlement for this project.

| API ID | Input / cached / output | Context / max output | Reasoning effort for proposed use | Published Build tier RPM / TPM |
|---|---|---|---|---|
| `gpt-5-mini` (baseline) | 0.25 / 0.025 / 2.00 | 400,000 / 128,000 | Current code uses low and minimal | 5,000 / 2,000,000 |
| `gpt-6-luna` | 0.10 / 0.01 / 0.50 | 1,050,000 / 128,000 | none or low initially; also medium/high/xhigh/max | 5,000 / 2,000,000 |
| `gpt-6.1-terra` | 1.00 / 0.05 / 5.00 | 1,050,000 / 128,000 | none or low initially; also medium/high/xhigh/max | 5,000 / 1,000,000 |
| `gpt-6.1-sol` | 2.00 / 0.10 / 10.00 | 1,050,000 / 128,000 | low/medium/high/xhigh/max; pro mode is a separate parameter | 5,000 / 1,000,000 |
| `gpt-6.2-sol` | 2.00 / 0.10 / 10.00 | 1,050,000 / 128,000 | low/medium/high/xhigh/max | 5,000 / 1,000,000 |
| `gpt-6.2-astra` | 8.00 / 0.40 / 40.00 | 1,050,000 / 128,000 | low/medium/high/xhigh/max | 5,000 / 1,000,000 |

All listed 6-family candidates document Responses, structured outputs, streaming and function calling. For 6.2 Sol/Astra and 6.1 Terra use Responses for tool calling; Luna also supports Chat function calling with none effort. None of MoneyBot's two requests invokes a tool. 6.2 does not accept `none` or `minimal`. Terra and Luna list `none`, omit `minimal`. For new 6.2 requests omit temperature, top_p and logprobs controls as specified in the guide. Do not silently drop parameters to make invalid production requests appear healthy. Some guides recommend medium for new integrations; test low/none first for these short, latency-sensitive schemas. [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) · [Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol) · [Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra) · [Pricing](https://developers.openai.com/api/docs/pricing)

## Workload decisions

| Workload | Current | First candidate / next comparison | Decision criterion |
|---|---|---|---|
| Quick Ask and Clearview prose | Shared mini default | Trial `gpt-6-luna` at low or none; keep Terra as a separately tested rollout/rollback alternative | Fixed facts, exact numbers and final action, schema completion, useful risks and latency. Short grounded explanation provides no evidence that Sol is necessary. |
| Portfolio with deterministic lock | Shared mini default; action is locked against the model | Compare local action-bound explanation and Luna before buying a more costly narrative. Phase A can keep the established request shape with Terra if validated. | Retain the authoritative action and matching reason; optional text cannot supply new trades. Do not cache private position advice across users. |
| Portfolio without a deterministic lock | Shared mini default; LLM can select an action | `gpt-6.1-terra` at low as official continuity candidate, with Luna as a lower-cost challenger subject to the **same** quantitative gate. Until acceptance, expose only confirmed rule authority or an unavailable state. | Independent owner evaluation of disagreements, missing data, suitability and evidence lineage. No automatic premium fallback. |
| Advanced research / optional premium explanation | No distinct dependency | Consider `gpt-6.2-sol` for bounded complex narrative only after a use case exists. Astra is an opt-in comparison for difficult synthesis, with a separate budget. | Require incremental factual/financial accuracy over Terra or Sol sufficient for the additional cost; empirical latency and quality unknown. |
| Automated watchlist explanations / classification / news sentiment | Clearview is user-triggered Quick Ask; no independent job or LLM sentiment classifier | No new production assignment. A future offline explanation could test Luna. Retain current quantitative sentiment logic. | Any new LLM feature used by scoring requires separate model/data validation. |
| Alpha Atlas scoring and certified evidence | Proprietary deterministic paths | Preserve the existing engine and all authorization gates. | No generative replacement or fallback. |

Financial reasoning, numerical consistency, instruction adherence, semantic schema validity, hallucination rate, p95 latency and comparative investment quality require testing. A schema-capable model can return a valid BUY enum for the wrong reason. Rank candidates by observed cost per **accepted** response under the same inputs, deadline and controls. Luna is positioned for high volume; Astra for difficult reasoning. Neither positioning nor context length establishes suitability. [Catalog](https://developers.openai.com/api/docs/models) · [Validation criteria](openai_migration_validation_plan.md)

## Request compatibility by path

| Path | Required for cutover | Optional later |
|---|---|---|
| Quick Ask | Preserve Responses, strict inline schema, string input and nested message extraction. Replace retry's minimal; set both attempt profiles deliberately. Validate exact text/array cardinality, completed or explicitly supported legacy envelope, refusal and exhausted output. Preserve accepted legacy response shapes where safe. | Stable developer message first if revising instructions; measure repeatable prefixes. No new tool, OpenAI streaming or conversation history required. |
| Portfolio | Preserve Responses, strict four-field contract, enum, two-by-two guidance, locked-action and suitability ordering. Both 420-token attempts currently send minimal. Check 420 includes reasoning, validate refusal and malformed output. Keep local JSON/NDJSON frame ordering, cancellation and timeout policy. | Per-workload breaker and usage controls. Consider removing redundant locked work only after contract and performance tests. |

The application does not set a provider system/developer role, tools or temperature. It reads raw response JSON, including nested `output[].content[]`; `response.output_text` convenience access in an SDK must not be assumed present on the wire. Keep Responses' `max_output_tokens`, not Chat's `max_completion_tokens`. Treat response-level errors, refusal content and incomplete status explicitly. If instructions are restructured for GPT-6.2, static developer messages can appear first in `input`; top-level instructions follow input. Background mode changes delivery, retention considerations and polling. No workload here requires it or an SDK upgrade. No bulk API rewrite is warranted. [Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create) · [Migration guide](https://developers.openai.com/api/docs/guides/latest-model) · [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) · [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)
