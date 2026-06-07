# MoneyBot User Profile + Real-Time Setup

**Status date:** June 7, 2026
**Current stage:** Profile integration closeout is complete; Massive REST normalization is the next implementation stage; real-time ingestion has not started.

This folder turns the larger [architecture roadmap](../user_profile_realtime_roadmap.md) into short implementation pages that can be updated as work is completed.

## Progress dashboard

| Page | Workstream | Status | Outcome |
| --- | --- | --- | --- |
| [1](01-current-state.md) | Current state and completed work | **Operational** | Versioned profiles, questionnaire, and portfolio suitability exist |
| [2](02-profile-closeout.md) | Finish user-profile integration | **Complete** | One reusable decision context across advice, notifications, and logs |
| [3](03-massive-rest-foundation.md) | Normalize Massive REST data | Not started | Fresh, timestamped, source-consistent quotes and bars |
| [4](04-realtime-stream-worker.md) | Add Massive WebSocket worker | Not started | Bounded subscriptions feeding shared Redis state |
| [5](05-live-ui-and-alerts.md) | Deliver live updates and triggers | Not started | Authenticated SSE, live portfolio prices, controlled alerts |
| [6](06-history-validation-rollout.md) | Historical validation and rollout | Not started | Reproducible datasets, walk-forward evaluation, safe promotion |

## Where we are now

```text
[Profile database/API]      DONE
[Settings questionnaire]    DONE
[Portfolio suitability]     DONE (first integration)
[Profile integration]       DONE
[Massive REST normalization]WAITING
[WebSocket + Redis]         WAITING
[SSE + live alerts]         WAITING
[Historical validation]     WAITING
```

### Overall completion definition

The user-profile real-time setup is complete when:

- Every personalized decision records the profile version and applied suitability rules.
- Missing profile answers always use visible conservative defaults.
- Massive data has normalized timestamps, freshness, session, source, and quality fields.
- A dedicated backend worker maintains a bounded Massive WebSocket connection.
- Redis provides shared latest-state data and subscription coordination.
- Portfolio and Quick Ask can receive authenticated, coalesced live updates.
- Recommendations recompute only on controlled triggers, not every tick.
- Stream gaps recover from REST snapshots automatically.
- Historical and personalized-policy changes pass walk-forward validation before promotion.
- Licensing, reliability, latency, and notification-volume gates are documented and satisfied.

## Update convention

When a task is completed:

1. Change its checkbox from `[ ]` to `[x]` on the relevant page.
2. Add the commit or PR reference beside the task when useful.
3. Update the status table above.
4. Record material decisions in that page's **Decision log** section.
5. Do not mark a page complete until all exit criteria pass.

## Recommended immediate order

1. Implement [Page 3 — Massive REST Foundation](03-massive-rest-foundation.md).
2. Run the WebSocket in shadow mode using [Page 4](04-realtime-stream-worker.md).
3. Add live browser delivery only after shadow metrics pass using [Page 5](05-live-ui-and-alerts.md).
4. Validate and roll out with [Page 6](06-history-validation-rollout.md).
