# Coworker Demo Trajectory DAG

Generated from `agent_trajectory_ground_truth.yaml`; do not edit by hand.

| Node | Tool | Scenarios | Preconditions |
|---|---|---|---|
| `TICKET_READ` | `observe` | normal, post_change_anomaly | reset |
| `PLAN_CREATED` | `task_planner` | normal, post_change_anomaly | TICKET_READ |
| `PRE_ALARM` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED |
| `PRE_PROBE` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED |
| `PRE_CAPACITY` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED |
| `PRE_RUNTIME` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED |
| `PRE_TRAFFIC` | `browser_click` | normal, post_change_anomaly | PLAN_CREATED |
| `PRE_CONFIG` | `browser_click` | normal, post_change_anomaly | TICKET_READ |
| `PRE_DECISION` | `sop_decide` | normal, post_change_anomaly | PRE_ALARM, PRE_PROBE, PRE_CAPACITY, PRE_RUNTIME, PRE_TRAFFIC, PRE_CONFIG |
| `PRE_PROGRESS` | `task_progress_check` | normal, post_change_anomaly | PRE_DECISION |
| `ADD_SUBMIT` | `browser_click` | normal, post_change_anomaly | PRE_DECISION |
| `ADD_WAIT` | `browser_wait` | normal, post_change_anomaly | ADD_SUBMIT |
| `ADD_GREP` | `terminal_execute` | normal, post_change_anomaly | ADD_WAIT |
| `IMPLEMENT_DECISION` | `sop_decide` | normal, post_change_anomaly | ADD_GREP |
| `IMPLEMENT_PROGRESS` | `task_progress_check` | normal, post_change_anomaly | IMPLEMENT_DECISION |
| `POST_ALARM` | `browser_click` | normal | IMPLEMENT_DECISION |
| `POST_PROBE` | `browser_click` | normal | IMPLEMENT_DECISION |
| `POST_CAPACITY` | `browser_click` | normal | IMPLEMENT_DECISION |
| `POST_RUNTIME` | `browser_click` | normal | IMPLEMENT_DECISION |
| `POST_TRAFFIC` | `browser_click` | normal | IMPLEMENT_DECISION |
| `BUSINESS_SUBMIT` | `browser_click` | normal | IMPLEMENT_DECISION |
| `BUSINESS_WAIT` | `browser_wait` | normal | BUSINESS_SUBMIT |
| `NORMAL_PROGRESS` | `task_progress_check` | normal | POST_ALARM, POST_PROBE, POST_CAPACITY, POST_RUNTIME, POST_TRAFFIC, BUSINESS_WAIT |
| `NORMAL_COMPLETE` | `sop_decide` | normal | NORMAL_PROGRESS |
| `ANOMALY_FOUND` | `browser_click` | post_change_anomaly | IMPLEMENT_DECISION |
| `ROLLBACK_DECISION` | `sop_decide` | post_change_anomaly | ANOMALY_FOUND |
| `REMOVE_SUBMIT` | `browser_click` | post_change_anomaly | ROLLBACK_DECISION |
| `REMOVE_WAIT` | `browser_wait` | post_change_anomaly | REMOVE_SUBMIT |
| `ROLLBACK_GREP` | `terminal_execute` | post_change_anomaly | REMOVE_WAIT |
| `ROLLBACK_PROGRESS` | `task_progress_check` | post_change_anomaly | ROLLBACK_GREP |
| `ROLLED_BACK` | `sop_decide` | post_change_anomaly | ROLLBACK_PROGRESS |
