# Alpha Atlas V4 Phase 1 corporate-action adjustment parity

**Status:** implementation behavior verified; historical-source completeness blocked.

- Raw daily bars remain immutable source evidence. Features are recomputed on an
  event-time split basis using only actions available by the feature cutoff.
- Forward, reverse, and composed splits preserve price/volume economic basis.
- A split known before or at the decision may normalize prior feature bars.
- A split between decision and entry, during the holding window, or near exit does
  not rewrite earlier features; it may adjust the realized entry-to-exit economic
  return under the execution contract.
- Entry remains the S0 regular-session open and exit remains the contracted S4/S9
  regular-session close. SPY and sector ETF histories follow the same cutoff rule.
- Cash dividends are not used in the current 43-feature price-return contract.
- Ticker-change plus split requires an effective-dated stable-identity chain. Missing
  action evidence, identity evidence, or executable exit fails closed.

The deterministic corporate-action and Phase 1 suites cover forward splits, reverse
splits, composed splits, future-action non-leakage, prediction-date boundaries,
effective-dated identity events, and missing reference/exit blockers. The remaining
technical blocker is demonstrating complete historical action/event coverage for
the intended universe; implementation tests cannot establish provider completeness.
