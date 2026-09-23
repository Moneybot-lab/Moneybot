# Alpha Atlas V4 cohort-relative ranking clarification v1

**Status:** `AUTHORIZED_FOR_NO_PERFORMANCE_AUDIT`
**Proposal SHA-256:** `000a0f8e2ba0fbaf563eb64dc3589fe4b2ae0695fd6c63fa8c536675d9b26f4d`
**Clarification JSON SHA-256:** `d439d2148ef00318db0c8daad7a64c65c23a3970c02dd7030a13d96d112c847f`

This clarification applies only to the new cohort-relative ranking experiment. It does not change the historical v1.1 specification or any historical result.

For the no-performance input audit, a ticker/date/horizon/entry/exit group is eligible only if every canonical observation passes all three saved gates (`abstained=false`, `risk_rejected=false`, and `rule_rejected=false`). Any explicit rejection makes the whole group ineligible. Missing, null, malformed, or uninterpretable gate evidence blocks validation and never defaults to pass.

The group score is the arithmetic mean of the finite saved scores for every canonical observation in the group. Passing members are never subsetted. Observation multiplicity does not increase the ticker group's cohort weight. Rejected groups and member-level gate reasons remain in the audit evidence.
