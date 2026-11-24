# Cost of keeping MoneyBot's two OpenAI workloads online

**Decision estimate.** For 1,000 active users, an illustrative month costs **$50.35** for Luna Quick Ask / Terra Portfolio, **$89.50** for Terra on both and **$179** for Sol on both, subject to the workload arithmetic below. These are conditional projections. Today's actual model, bill, account tier, token distribution and retry fraction remain unverified.

## Prices and demand assumptions

Standard USD per million tokens for uncached input / cache read / total output are GPT-5 mini **0.25 / 0.025 / 2**, Luna **0.10 / 0.01 / 0.50**, Terra **1 / 0.05 / 5**, 6.2 Sol **2 / 0.10 / 10**, and 6.2 Astra **8 / 0.40 / 40**. 6.1 Sol shares 6.2 Sol's standard rates. Use exact IDs in the [replacement matrix](openai_model_replacement_matrix.md). No tools, long context, regional premium, cache writes, Priority/Fast, separate storage, market-data or hosting charges in the base case. Verified November 24, 2026. [API pricing](https://openai.com/api/pricing/) · [Detailed token prices](https://developers.openai.com/api/docs/pricing)

For **each active user per month**, assume 10 explicit Quick Ask misses, 4 Clearview refreshes with 5 eligible cache-miss symbols each, and 4 portfolio enrichments with 5 eligible lots each. Thus 30 Quick-family calls and 20 Portfolio calls, 50 total attempts with one paid attempt per accepted output in the base case. At 10,000 users this is 500,000 model calls per month. No calls are assigned to server-side scheduled tasks. All portfolio enrichment is charged to its initiating user, including the work after the first NDJSON frame.

| Work unit | Input | Visible + reasoning = billed output | Mix |
|---|---:|---:|---:|
| Quick Ask / Clearview | 600 | 120 + 50 = 170 | 60% |
| Per portfolio lot | 800 | 150 + 150 = 300 | 40% |

The token split is a comparison assumption, including for low/no-reasoning alternatives; actual billed output can change materially. Paid output already includes internal reasoning. Charge each reported output token once. Compute cost as (uncached input × input rate + cache reads × read rate + cache writes × write rate + output × output rate) / 1,000,000, using disjoint input categories. For the table, all input is ordinary uncached input. Prices represent one request to one model, not an entire multi-lot page.

## Comparable unit costs

| Routing policy | Quick/request $ | Portfolio/request $ | Blended/request $ | Per 1,000 requests $ | Per 10,000 requests $ |
|---|---:|---:|---:|---:|---:|
| Existing enabled defaults | 0.000490 | 0.000800 | 0.000614 | 0.614 | 6.14 |
| Direct official replacement | 0.001450 | 0.002300 | 0.001790 | 1.790 | 17.90 |
| Primarily GPT-6.2 | 0.002900 | 0.004600 | 0.003580 | 3.580 | 35.80 |
| Workload-specific | 0.000145 | 0.002300 | 0.001007 | 1.007 | 10.07 |
| Selective premium | 0.000145 | 0.003910 | 0.001651 | 1.651 | 16.51 |
| Luna challenger (both) | 0.000145 | 0.000230 | 0.000179 | 0.179 | 1.79 |

“Existing” assumes enabled `gpt-5-mini` on both lanes. With the checked-in disabled configuration and no override, provider spend is $0. “Direct” uses OpenAI's `gpt-6.1-terra` for both. “Primarily GPT-6.2” uses `gpt-6.2-sol` throughout. “Workload-specific” uses `gpt-6-luna` for Quick/Clearview and Terra for Portfolio. “Selective premium” prices 10% of portfolio calls at `gpt-6.2-astra` instead of Terra, keeping work volume fixed. This is a sensitivity case, not permission to route investment actions to Astra. “Luna challenger” shows the lowest-priced candidate in this comparison, with financial quality still untested.

## Monthly exposure in USD

| Routing policy | 1 active user | 100 | 1,000 | 10,000 |
|---|---:|---:|---:|---:|
| Existing enabled defaults | 0.0307 | 3.07 | 30.70 | 307.00 |
| Direct official replacement | 0.0895 | 8.95 | 89.50 | 895.00 |
| Primarily GPT-6.2 | 0.1790 | 17.90 | 179.00 | 1,790.00 |
| Workload-specific | 0.0504 | 5.04 | 50.35 | 503.50 |
| Selective premium | 0.0826 | 8.26 | 82.55 | 825.50 |
| Luna challenger (both) | 0.0090 | 0.90 | 8.95 | 89.50 |

Each column assumes the same volume per active user, mix, token counts and prices. All spend in this table is user-initiated. Scheduled background baseline is **$0** at all four sizes. A hypothetical new offline job of 100 short explanations per day for 30 days at Quick-shaped Luna rates would cost **$0.435/month** Standard or **$0.2175/month** at Batch rates; no such job was discovered. If AI is enabled but a request is skipped or served from the application cache, no new OpenAI charge is assigned.

## Sensitivity and break-even

- A five-lot Terra view is $0.0115 at the stated tokens; 28 lots cost $0.0644 for one attempt each. A 20-symbol Clearview refresh costs $0.0029 on Luna before application cache hits. A seven-day billing export with only model, day, attempt count, input, cached input, output and cost buckets would replace these hypotheses.
- One historical November 3 portfolio request made 56 attempts for 28 usable enhancements. The record lacks token usage. At 10% extra fully billed attempts of similar size across all calls, multiply the estimates by 1.10. Doubling attempts doubles this simplified estimate. A timeout can be billable if generation completed remotely; a rejected 400 need not have the same cost. Do not extrapolate the historical retry ratio to the entire user base. [Historical aggregate](../portfolio_ai_latency_repair.md#production-evidence-and-root-cause)
- Each additional 100 billed reasoning tokens costs $0.00005 on Luna, $0.0005 on Terra, $0.001 on Sol or $0.004 on Astra. At the portfolio token assumption Astra costs $0.0184/lot versus Terra $0.0023, an incremental $0.0161 per accepted premium lot before retries. Two premium lots per user per month add $0.0322/user. A 10,000-user rollout must recover at least $322 extra monthly contribution to pay that inference difference. Price, conversion, churn, support costs and gross margin are unknown.
- A repeated 1,024-token stable prefix on Terra costs $0.001024 as uncached input, $0.00128 as a 30-minute write and $0.0000512 per cache read. One write plus one hit costs $0.0013312 versus $0.002048 for two uncached passes. Prefix must qualify and hit before expiration. Both illustrative prompts fall below the 1,024-visible-token minimum for these 6-family models; model **zero** prefix savings in the base case. Check actual cache writes/reads and retention. Do not pad dynamic position data to obtain a discount. Batch offers 50% lower token rates with up to a 24-hour window, unsuitable for interactive advice. [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) · [Batch API](https://developers.openai.com/api/docs/guides/batch) · [Reasoning token treatment](https://developers.openai.com/api/docs/guides/reasoning)
- At low scale, deterministic Clearview rationales and avoiding paid work for locked rows can remove full calls. Confirm that existing UI guidance and decision-event contracts survive. Do not deduplicate two lots with different basis, quantity, profile or data versions. Quick Ask's cache key omits quote/sentiment freshness, model/profile version and some prompt inputs. Bound cache size, include an input fingerprint and invalidate on cutover. Prefer suppressing duplicate in-flight *identical* work to sharing positions between accounts.
- Base demand at 10,000 users averages about 12 attempts/minute over a 30-day month. An assumed 100× burst is roughly 1,157 RPM and 1.04M total tokens/minute at the blended 902 tokens/call (rate-limit accounting can reserve the output ceiling). Per-model tier and per-minute budget, concurrency, admission and day-to-day timing matter more than the monthly average. Track cost per accepted output, tier limits, fallback and retry rates by workload. Review spend and p95 before expanding. [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)

No measured production spend, premium feature adoption, live model benchmarks or paid test requests underlie these estimates.
