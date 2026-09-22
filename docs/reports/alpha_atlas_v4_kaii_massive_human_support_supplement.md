# Alpha Atlas V4 KAII Massive human-support supplement

- Recorded in repository: `2026-09-21` (UTC recording date only).
- Response date/time: `UNKNOWN_NOT_SUPPLIED`.
- Source: user-supplied correspondence attributed to **Massive human support**.
- Agent name / ticket ID: not supplied.
- Linked evidence: diagnostic `35233428008-1`; evaluation `35255222521-1`.
- Original reports and artifact hashes: unchanged.

## Verbatim response 1

```text
Thanks for reaching out! For your questions:
Per [https://massive.com/docs/rest/stocks/market-operations/condition-codes](https://massive.com/docs/rest/stocks/market-operations/condition-codes), condition 41 updates both price and volume
I pull the full day of quotes for KAII and found there are only 52 of them. It is indeed expected that there is very few/no trade for a ticker with such a small visibility
Hope this helps!
```

## Verbatim response 2

```text
Hello again. Yes I pulled the pull day for KAII and there were only 52 quotes and no trades. This ticker is extremely illiquid.

For the conditions question, if a trade includes any condition that should be excluded from aggregates, the trade is excluded regardless of what other conditions it has. That means any odd lot trade is excluded from aggregation.

Hope this helps!
```

## Findings supported by the correspondence

1. Condition 41 updates price and volume. It alone does not explain an absent OHLC bar.
2. A restrictive condition controls a mixed-condition trade: an odd-lot trade is excluded from aggregation even when another condition permits an update.
3. In the saved `2023-01-19` regular-session evidence, the `[17,37,41]` trade is excluded because condition 37 is odd lot. The separate `[16]` interpretation remains supported only by the saved condition table, not independently by this correspondence.
4. Both saved `2023-02-17` `[14,12,37,41]` trades are excluded because condition 37 is odd lot.
5. Human support reports a full-day KAII query with 52 quotes and no trades, but neither response names a date. Its status is therefore `DATE_ATTRIBUTION_UNCONFIRMED`; it is not attributed to February 24. The independently saved February 24 full-day API response remains empty with pagination complete.

## Qualifications retained

- The combination rule is confirmed by human support, but its historical version and applicability to the currently served 2023 aggregates remain `UNVERIFIED/UNKNOWN`.
- January 19 remains limited to the saved regular-session query scope.
- Provider testimony is not a locally reproduced API response and provides no query bounds, timezone, entitlement, pagination, response hash, or complete-venue-coverage evidence.
- Quotes do not establish trades or a price. No replacement bar, terminal proceeds, zero recovery, or valuation readiness is inferred.
- Full historical coverage and terminal valuation remain open.
