# Alpha Atlas V4 registered weighting comparison

Experiment `alpha-atlas-v4-big-loss-weight-ablation.v1` is a two-arm,
development-only analysis of the frozen `challenger-big-loss-avoider-v1` recipe.
The current arm uses its existing return-bucket weights (1 normally, 2 for loss,
6 for big loss). The uniform arm assigns every fold-training observation the
current arm's mean raw weight; this preserves total raw weight, although the
deterministic fitter also normalizes supplied weights to mean one. No separate
class weighting remains active.

All observations, labels, features, fold memberships and ordering, fold-local
fill policy, logistic family, learning rate, L2, 0.60 threshold, epochs,
deterministic initialization, lack of calibration, and signal/execution rules
remain fixed. Reporting weights never enter fitting. Final-holdout rows are
removed before preparation and are never evaluated.

The primary endpoint is per-fold symbol/original-event-date-balanced Brier loss.
The paired difference is uniform minus current and the aggregate is the
equal-weight mean across exactly three folds. Favorable descriptive research
evidence requires a negative aggregate and negative differences in at least two
folds. Every fold is reported; three folds provide no significance claim and
repeated observations are not independent bets. This rule cannot select a model,
change a threshold, or promote an artifact.

The immutable artifact-bound JSON registration must be created before execution.
It records verified input, plan, manifest, capture, recipe, feature, target, and
exact fold-ID hashes. The repository cannot truthfully commit that final JSON
until the frozen manifest/input/capture files are supplied; the registration
command fails rather than inventing those values.
