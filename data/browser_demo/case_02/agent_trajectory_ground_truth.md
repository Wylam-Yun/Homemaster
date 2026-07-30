# Generic Browser Change-Ticket Trajectory GT

Generated from `agent_trajectory_ground_truth.yaml`; do not edit by hand.

Ticket SHA-256: `e76b2d5f7ee4b574e629129c32b140cdc784bea1eb1ebf99c0ecf1d78e0f49bb`

Execution status: normal implementation `VERIFIED`; full normal `UNVERIFIED`; post-change anomaly/rollback `UNVERIFIED`.

| Node | Tool | Scenarios | Preconditions | Review backfill |
|---|---|---|---|---|
| `SKILL_LOADED` | `load_skill` | normal, post_change_anomaly |  | - |
| `TICKET_READ` | `web_fetch` | normal, post_change_anomaly | SKILL_LOADED | - |
| `PLAN_CREATED` | `task_planner` | normal, post_change_anomaly | TICKET_READ | - |
| `PRE_ALARM` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED | required |
| `PRE_PROBE` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED | required |
| `PRE_CAPACITY` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED | required |
| `PRE_RUNTIME` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED | required |
| `PRE_TRAFFIC` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED | required |
| `PRE_CONFIG` | `browser_inspect` | normal, post_change_anomaly | TICKET_READ | required |
| `PRE_UPSTREAM` | `browser_inspect` | normal, post_change_anomaly | TICKET_READ | required |
| `PRE_PROGRESS` | `task_progress_check` | normal, post_change_anomaly | PRE_ALARM, PRE_PROBE, PRE_CAPACITY, PRE_RUNTIME, PRE_TRAFFIC, PRE_CONFIG, PRE_UPSTREAM | - |
| `ADD_FIELDS` | `browser_fill` | normal, post_change_anomaly | PRE_PROGRESS | required |
| `ADD_SUBMIT` | `browser_click` | normal, post_change_anomaly | ADD_FIELDS | required |
| `ADD_WAIT` | `browser_wait` | normal, post_change_anomaly | ADD_SUBMIT | required |
| `ADD_GREP` | `bash` | normal, post_change_anomaly | ADD_WAIT | required |
| `IMPLEMENT_PROGRESS` | `task_progress_check` | normal, post_change_anomaly | ADD_GREP | - |
| `POST_ALARM` | `browser_click` | normal | IMPLEMENT_PROGRESS | required |
| `POST_PROBE` | `browser_click` | normal | IMPLEMENT_PROGRESS | required |
| `POST_CAPACITY` | `browser_click` | normal | IMPLEMENT_PROGRESS | required |
| `POST_RUNTIME` | `browser_click` | normal | IMPLEMENT_PROGRESS | required |
| `POST_TRAFFIC` | `browser_click` | normal | IMPLEMENT_PROGRESS | required |
| `BUSINESS_FIELDS` | `browser_fill` | normal | IMPLEMENT_PROGRESS | required |
| `BUSINESS_SUBMIT` | `browser_click` | normal | BUSINESS_FIELDS | required |
| `BUSINESS_WAIT` | `browser_wait` | normal | BUSINESS_SUBMIT | required |
| `NORMAL_PROGRESS` | `task_progress_check` | normal | POST_ALARM, POST_PROBE, POST_CAPACITY, POST_RUNTIME, POST_TRAFFIC, BUSINESS_WAIT | - |
| `NORMAL_COMPLETE` | `browser_click` | normal | NORMAL_PROGRESS | required |
| `ANOMALY_FOUND` | `browser_click` | post_change_anomaly | IMPLEMENT_PROGRESS | required |
| `ROLLBACK_PLANNED` | `task_progress_check` | post_change_anomaly | ANOMALY_FOUND | - |
| `REMOVE_FIELDS` | `browser_fill` | post_change_anomaly | ROLLBACK_PLANNED | required |
| `REMOVE_SUBMIT` | `browser_click` | post_change_anomaly | REMOVE_FIELDS | required |
| `REMOVE_WAIT` | `browser_wait` | post_change_anomaly | REMOVE_SUBMIT | required |
| `ROLLBACK_GREP` | `bash` | post_change_anomaly | REMOVE_WAIT | required |
| `ROLLBACK_COMPLETE` | `browser_click` | post_change_anomaly | ROLLBACK_GREP | required |
