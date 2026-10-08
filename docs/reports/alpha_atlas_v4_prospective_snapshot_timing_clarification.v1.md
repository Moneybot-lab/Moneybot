# Alpha Atlas V4 prospective snapshot timing clarification v1

This clarification is bound to contract JSON SHA-256
`09bf4a7308533195df5a0be4277cba7a5a3bbcd15b5a6fd28abc4321380ad692`.
It changes no other eligibility rule.

Using `America/New_York` and repository calendar `XNYS-rule-calendar.v1`, the
approved static pilot universe remains identical for ten sessions and is bound
to a session manifest at 07:45. Assembly runs 07:45–08:30. A snapshot must be
complete, durably persisted, and ready for use by **08:40 inclusive**. At 08:45,
the decision boundary uses the already-frozen eligible cohort. Readiness after
08:40 is late—including readiness between 08:40 and 08:45—and cannot replace an
on-time snapshot or change that cohort. Delayed jobs neither move deadlines nor
backdate readiness. Intended entry remains the official regular open and the
contract's five-session exit convention is unchanged.
