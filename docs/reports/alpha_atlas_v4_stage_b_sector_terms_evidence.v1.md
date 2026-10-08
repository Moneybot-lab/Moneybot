# Alpha Atlas V4 Stage B sector and subscription-terms evidence v1

**Decision:** dated AAPL→XLK evidence is supported; applicable account terms remain unavailable. No authorization was created.

## AAPL→XLK evidence

Three distinct propositions are recorded:

1. **Sector classification.** S&P Dow Jones Indices showed Apple Inc. (`AAPL`) in Information Technology as of **2026-08-31**. The public indexed rendering was inspected on 2026-10-06; direct byte retrieval returned HTTP 403, so no source-byte hash is claimed.
2. **XLK membership.** State Street's official XLK page listed `APPLE INC`, 52,443,075 shares, at 13.22% weight as of **2026-10-05**. The 236,143 retrieved bytes had SHA-256 `f2628b7b699dd444f195aa2f296c5fe3c1e67b31523ba88bb29eee384ffc9a8e`.
3. **Experiment proxy choice.** State Street describes XLK as tracking the Technology Select Sector Index, intended to represent the S&P 500 technology sector. Separately, the registered fixture explicitly selects XLK for AAPL. Thus XLK is a deliberate sector proxy, not an assertion that it is the only possible benchmark.

Sources: [State Street XLK](https://www.ssga.com/us/en/individual/etfs/state-street-technology-select-sector-spdr-etf-xlk) and [S&P Global 1200 Information Technology](https://www.spglobal.com/spdji/en/indices/equity/sp-global-1200-information-technology-sector/).

This evidence is **dated only**. The October 5 holding does not prove availability before that day's 07:30 acquisition cutoff, a continuous interval, future membership, or security identity. Request 4 remains responsible for provider security identity. When a future session is chosen, the minimum check before request 1 is to preserve and hash authoritative classification/membership evidence whose stated date covers that session, or a methodology/effective-change record that establishes applicability. No replacement session is selected here.

## Massive terms

**Classification: `APPLICABLE_TERMS_UNAVAILABLE`.**

Saved account evidence establishes Stocks Advanced Individual, but the repository does not contain the account's accepted Order Form/checkout terms, accepted/versioned Individuals and Market Data Terms record, or any equities third-party addendum. Those documents matter because the current public hierarchy places the Order Form and Additional Terms above the Individuals Terms, while the Market Data Terms state that they control conflicts.

Current public documents establish the following, but do not substitute for the missing account record:

- The [Individuals Terms](https://massive.com/legal/individuals-terms-of-service) grant personal, non-business, non-commercial access and incorporate Market Data Terms. Sections 10 and 15.2 define the agreement components and precedence.
- The [Market Data Terms](https://massive.com/legal/market-data-terms-of-service) permit personal use, prohibit redistribution/commercial use, require confidentiality, and in section 5(d) restrict non-display use and derivative works unless licensed.
- Private owner-controlled storage and one access-restricted backup are not independently identified as requiring a separate consent letter. The copy/transmission restriction is framed around publication, distribution, or business/commercial use. No reviewed clause imposes a 180-day maximum. Section 8 nevertheless requires deletion and cessation of use after termination.
- The current [Stocks page](https://www.massive.com/stocks) markets backtesting, quantitative, and model-training uses and labels Individual plans personal/non-professional. Marketing does not prove that this account's Order Form supplies the license referenced by section 5(d).

Retrieved public bytes were hashed in the JSON record but intentionally not committed. They remain temporary/unpreserved; a URL is not treated as continuing preservation.

The missing document—not a blanket permission letter—is the account's accepted Stocks Advanced Individual Order Form/agreement/version record. Obtain and review it first. Only if it does not resolve whether section 5(d)'s “unless licensed” condition covers private non-display feature/model R&D should a narrow consent question be drafted. No support message was sent.

## Readiness effect

- AAPL→XLK: resolved for the dated October 5 evidence only; a future date requires a fresh applicability check.
- Massive terms: not resolved because the applicable account agreement/Order Form is unavailable.
- These two checks therefore do **not** yet permit preparation of a future Stage B execution authorization.
- Smallest next action: export the account's accepted Stocks Advanced Individual Order Form/agreement/version record for review.

Stage B remains `NOT_AUTHORIZED / NOT_EXECUTED`; the October 5 fixture remains expired; readiness remains `COMPLETE — BLOCKED_STORAGE_AND_CACHE`; the pilot remains disabled.
