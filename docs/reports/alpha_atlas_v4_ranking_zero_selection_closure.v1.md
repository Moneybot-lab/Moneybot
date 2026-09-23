# Alpha Atlas V4 ranking zero-selection closure v1

- **Status:** `COMPLETE` from investigation run `35815996007-1`.
- **Classification:** `EXPECTED_FROZEN_ZERO_SELECTION`.
- **Scope:** 30,819 assignments; three candidates × three folds; 10,273 observations per candidate.
- **Reconciliation:** producer selections 0; diagnostic selections 0; missing/uninterpretable 0; abstention, risk rejection, and rule rejection all 0; frozen threshold 0.60.
- **Semantics:** the two ranking-lane candidates are `ranking_score_not_buy_probability`; `challenger-ranking-top5-model-v1` is `probability`.
- **Conclusion:** no diagnostic interpretation defect was established and no corrected scoring is required. The old gross difference is zero exposure minus a negative equal-weight timing-cohort baseline, not successful stock selection. Expected behavior does not establish an effective selection design.
- **Historical note:** the original report's generic next action applied only if an interpretation defect was found. That condition was not met. The historical report remains unchanged.
- **Validation boundary:** public GitHub metadata validates run 35815996007 attempt 1, commit `4f55d3ca3fcbb9747b5f6499daec9e1f354a0e97`, success, artifact ID `10732045472`, and artifact digest `sha256:dbf53e33b4aa03be99369a38b977231701c28ec5d8bad4f5356a2f0cf67382c3`. Local authentication was unavailable, so individual historical report bytes/checksums were not independently downloaded or rehashed.
