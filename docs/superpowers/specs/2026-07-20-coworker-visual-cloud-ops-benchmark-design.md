# Change Coworker Visual Cloud-Ops Benchmark Design

## 1. Status

- Design status: approved direction, implementation pending.
- Target case: `data/coworker_demo/case_02`.
- Target runtime: the existing Change Coworker FastAPI environment and Homemaster agent harness.
- This document defines product behavior and benchmark contracts. It deliberately does not lock the final Python file decomposition because concurrent project changes may alter implementation boundaries.

## 2. Problem

The current benchmark proves that the agent can follow the SOP, call tools and satisfy a deterministic evaluator, but its browser surface is too shallow:

- Five monitoring checks are buttons on one page.
- Clicking a monitor button renders raw JSON and a semantic status label.
- The agent can complete monitoring checks without using model vision.
- Automation inputs are pre-populated from hidden run state, so the agent does not ground and enter the SOP parameters itself.
- The browser exposes too few meaningful pages, choices and distractors to resemble a cloud operations workflow.
- A click commonly produces text instead of a realistic chart, job view or configuration view.

The benchmark must become a small but coherent cloud-operations product while retaining deterministic state, exact DOM control and independently verifiable grading.

## 3. Goals

The first release must:

1. Present four coherent platform modules and eight agent-accessible pages.
2. Render monitoring evidence as readable charts rather than raw JSON.
3. Require screenshot understanding for metric and anomaly judgments.
4. Keep form controls accessible through exact `data-bid` DOM operations.
5. Require the model to select and enter all task-relevant monitor and automation parameters.
6. Preserve the locked `case_02` ticket, scenario outcomes and historical checkpoint identities.
7. Support both normal completion and post-change anomaly/rollback through the same pages.
8. Add realistic but deterministic distractors without making grading stochastic.
9. Reuse the existing FastAPI service, episode state, DAG, scoring, headed Chrome, recorder and observer.
10. Run without Docker, an external database, Grafana, Prometheus or a separate frontend service.

## 4. Non-Goals

The first release will not:

- Create a generic multi-tenant SRE benchmark unrelated to the locked change ticket.
- Import a complete observability backend or run real metric collectors.
- Replace DOM clicks with coordinate-based control.
- Add a visual-vs-DOM ablation study.
- Randomize metric truth, required parameters or evaluator outcomes.
- Add arbitrary shell access, arbitrary URLs or developer tools to the agent.
- Expose observer-only state, scenario labels, causal ground truth or scores to agent pages.
- Rebuild the application in React, Vue or another SPA framework.
- Make every navigation element a scored DAG node.

## 5. Authoritative Case Data

The locked `case_02` dataset remains the source of truth for business variables, SOP ordering and required outcome evidence.

### 5.1 Locked business variables

```text
TenantId       = tenanttenanttenant000198
ItemCode       = read
SpecCode       = ext.read.type1
ExtensionName  = read-ext
```

### 5.2 Locked target

```text
region              = cn-test-1
cluster             = billing-control-01
cloud service       = billing-module
resource bucket     = resource-bucket
business timestamp  = 2025122716
```

Historical access evidence contains the older names `region-a` and `cluster-alpha`. Those values must not be presented as the active episode target. The active browser workflow uses the locked current scenario values above. Historical names may appear only in clearly labelled archival material outside current evidence.

### 5.3 Required monitor domains

The five monitor pages map directly to the validation-set tools:

```text
QueryAlarmAndSlaStatus          -> Alarm & SLA
QueryProbeStatus                -> Probe status
QueryClusterCapacity            -> Cluster capacity
QueryComponentRuntimeMetrics    -> Component runtime metrics
QueryApiAndGatewayTraffic       -> API and gateway traffic
```

### 5.4 Required scripts

The implementation and rollback script remains `svc_cfg_cli_runner`. The business verification script remains `svc_usage_record_fetcher`. Parameter order and exact compound argument formats remain part of the evaluator contract.

## 6. Open-Source Reuse

The implementation reuses two frontend projects as libraries, not as services or benchmark foundations.

### 6.1 Tabler

[Tabler](https://github.com/tabler/tabler) is an MIT-licensed Bootstrap dashboard UI kit. It supplies the shared application shell and standard presentation components:

- top navigation and sidebar;
- breadcrumbs and page headings;
- cards and summary bands;
- forms, selects and validation states;
- tables, badges, tabs, modals and timelines;
- responsive desktop layouts.

Only pinned compiled distribution assets and adapted markup are vendored. The benchmark does not run the Tabler development server and does not require npm or Docker at runtime.

### 6.2 Apache ECharts

[Apache ECharts](https://github.com/apache/echarts) is an Apache-2.0 JavaScript visualization library. It supplies:

- time-series lines and areas;
- stacked bars;
- threshold lines and bands;
- change-event markers;
- legends, axes and deterministic annotations;
- Canvas rendering.

Only a pinned compiled distribution asset is vendored. Runtime pages do not use a CDN. ECharts receives deterministic public chart data from the existing FastAPI service and does not own benchmark state.

### 6.3 Reuse boundary

```text
Existing FastAPI/Homemaster  -> truth, state, validation, evidence, scoring
Tabler                       -> site shell and standard UI components
ECharts Canvas               -> screenshot-readable monitoring evidence
```

No complete open-source monitoring product is forked. Grafana, OpenObserve and similar systems would add services, data-source integration and deployment work without replacing the benchmark-specific evaluator.

## 7. Information Architecture

The agent sees four platform modules containing eight task-relevant pages. They share one visual shell and one run identity.

| Module | Page | Canonical URL shape |
|---|---|---|
| Change Center | Change ticket | `/ticket/{run_id}` |
| Configuration Center | Extension configuration | `/config/{run_id}` |
| Cloud Monitor | Alarm & SLA | `/monitor/{run_id}/alarm` |
| Cloud Monitor | Probe status | `/monitor/{run_id}/probe` |
| Cloud Monitor | Cluster capacity | `/monitor/{run_id}/capacity` |
| Cloud Monitor | Component runtime | `/monitor/{run_id}/runtime` |
| Cloud Monitor | API/Gateway traffic | `/monitor/{run_id}/traffic` |
| Automation Center | Script execution and jobs | `/automation/{run_id}` |

The read-only observer remains outside the agent navigation allowlist. API documentation, audit, score and hidden scenario routes remain unavailable to the agent.

All eight pages use the same Tabler-derived shell. The shell provides consistent navigation, run context, breadcrumbs and page hierarchy, but it must not display ground-truth conclusions.

## 8. Page Contracts

### 8.1 Change ticket

The ticket page presents only locked ticket content and current-run public identity:

- title, service, risk and target scope;
- locked variables;
- pre-change checks;
- implementation and verification instructions;
- post-change checks;
- business verification;
- rollback instructions;
- change history and approval presentation.

It does not execute configuration or monitoring checks. It is the source from which the model grounds parameters used on other pages.

### 8.2 Extension configuration

The configuration page closes the existing `PRE_CONFIG` evidence gap. Initial task fields are empty and use non-semantic placeholders.

Required controls:

```text
region, cluster, TenantId, ItemCode, SpecCode, ExtensionName
```

The result is a structured configuration view rather than JSON. It may show current mapping, upstream readiness, version, last update and effective-node count. Configuration results may be DOM-readable because the vision requirement applies to monitor charts, not all business records.

The backend accepts the checkpoint only when the submitted values exactly match the locked ticket and current target.

### 8.3 Monitor page family

Five routes use one common page contract and configuration-driven chart definitions. Each page contains:

- breadcrumbs and monitor navigation;
- empty or neutral filters;
- a query button;
- a query-completion indicator without a health conclusion;
- one primary chart area above the fold;
- optional secondary charts above the fold;
- a small deterministic distractor area where appropriate.

Common filters include:

```text
region, cluster, service, time range
```

Page-specific filters may add alarm state, probe group, capacity mode, component/instance or gateway/API. Correct task values are not preselected. Select options include a small fixed set of plausible distractors.

### 8.4 Automation center

The automation page begins with no task-specific parameter values selected or entered. It contains script selection, execution target, ordered parameters, submission, current job progress and job history.

For `svc_cfg_cli_runner`, the model must supply or select:

```text
execution region
execution cluster
control-manager target node
execution user = root
script parameter 1 = svc_cli_user
script parameter 2 = credential alias
script parameter 3 = billing-module
script parameter 4 = add or remove
script parameter 5 = extendChargeItem
script parameter 6 = exact compound business argument
```

For add, parameter 6 is:

```text
${TenantId}||${ItemCode}||${SpecCode}||${ExtensionName}
```

For remove, parameter 6 is:

```text
TenantId=${TenantId}||ItemCode=${ItemCode}
```

The credential is represented by a safe alias selector. The browser and model never receive the underlying secret.

For `svc_usage_record_fetcher`, the model must enter the ordered parameters:

```text
resource-bucket
2025122716
0
```

Submission produces a structured job view, not raw JSON:

- queued/running/succeeded/failed timeline;
- exact job ID;
- safe parameter confirmation;
- target node;
- execution steps;
- return code;
- configuration diff or business-record table.

The business-record view makes the eighth `itemKey` field and thirteenth `factor` field visually locatable without reducing the result to a single pass/fail sentence.

## 9. Chart Design

Charts use explicit axes, units, legends, threshold labels and static mark points so that a single screenshot is interpretable without mouse hover.

### 9.1 Alarm & SLA

Primary visuals:

1. SLA success-rate line around the `99.99%` objective.
2. Metric-delay time series with a `60 s` threshold.
3. Change execution marker and alert event markers.

Normal post-change data keeps metric delay below threshold. Anomaly post-change data raises metric delay from approximately `30 s` to `120-180 s` after the change marker. SLA may remain above `99.99%`; the active causal delay signal alone must block continuation.

### 9.2 Probe status

Primary visuals:

1. Probe availability percentage.
2. P95 probe response latency.

Normal and anomaly scenarios keep probes healthy, apart from benign deterministic variation. This prevents a simplistic rule that treats every page as abnormal after the change.

### 9.3 Cluster capacity

Primary visuals:

1. Current resource utilization.
2. Predicted utilization if unmodified nodes carry full traffic.
3. Capacity safety threshold, such as `85%`.

The locked scenario presents sufficient headroom, for example current load near `54%` and failover projection near `78%`.

### 9.4 Component runtime metrics

The page uses a compact two-by-two chart grid:

- P95 processing latency;
- error rate;
- request throughput;
- queue backlog.

Normal data remains stable. Anomaly post-change data raises latency and backlog after the change marker while error rate and throughput remain plausible. This supplies a second visual source that correlates with the metric-delay alert.

### 9.5 API and gateway traffic

Primary visuals:

- request volume/QPS;
- success rate;
- stacked `2xx/4xx/5xx` series.

Both scenarios show benign variation without a causal post-change failure. A small unrelated warning or historical spike may appear as a distractor but must be clearly separated in time or service identity.

## 10. Deterministic Presentation Data

Metric arrays are deterministic presentation fixtures derived from:

```text
scenario kind + episode stage + monitor page + selected filters
```

They are not sampled randomly at runtime. The same run scenario and submitted query produce the same chart series.

Correct filters show the locked service and target evidence. Incorrect but valid filters show plausible deterministic distractor data rather than a validation error that reveals the correct answer. Missing or invalid fields produce ordinary form validation and no query evidence.

Public chart payloads contain only values that a legitimate monitoring frontend would receive. They do not include fields such as `scenario_kind`, `causal`, `expected_decision`, evaluator node IDs or semantic health verdicts.

## 11. Visual-Evidence Contract

The benchmark uses hybrid interaction:

- DOM for navigation, form filling, selection and clicking.
- Screenshot/VLM input for chart interpretation.

### 11.1 No semantic DOM shortcut

After a monitor query:

- chart values are drawn into ECharts Canvas;
- no raw JSON is placed in visible text;
- no DOM table mirrors plotted metric values;
- no badge says `healthy`, `active alarm`, `rollback required` or an equivalent conclusion;
- no hidden `data-*`, ARIA description or script-exposed evaluator label contains the conclusion;
- the query receipt exposes only completion identity, page revision and evidence references.

DOM observation may reveal chart titles, filters and control labels, but it must not reveal the plotted values or conclusion.

### 11.2 Screenshot tool

Add a model-selectable `browser_screenshot` operation. It captures the current native viewport, saves a run-owned image and returns an image content reference through the existing multimodal message path.

The screenshot evidence record binds:

```text
run ID
current URL/page kind
page state version
latest chart revision
source query action ID
submitted filter fingerprint
image artifact path and hash
```

A screenshot taken before the correct query, from another monitor page, or after a later query cannot satisfy the checkpoint. The primary charts must fit above the fold at the benchmark viewport size.

### 11.3 Decision grounding

Monitor checkpoints require both:

1. an accepted query with exact required filters; and
2. a linked screenshot of that rendered chart revision delivered to the model.

An anomaly or rollback decision must reference current-run post-change screenshot evidence showing the causal signal. Model narration alone cannot replace it.

## 12. Agent Action Contract

The browser allowlist expands from three route families to the eight agent pages. Navigation remains symbolic and restricted; arbitrary URLs remain forbidden.

Every actionable control retains one unique, visible and enabled `data-bid`. DOM tools continue to verify input/select readback. Monitor and automation query receipts must not return business truth that bypasses the page.

The initial value contract is:

- ticket fields: readable locked source data;
- monitor filters: empty or neutral, with no correct task target preselected;
- configuration query fields: empty;
- automation script, operation, target and parameters: empty or explicit `Select...` state;
- credential secret: never exposed; only aliases are selectable.

## 13. Evaluation Compatibility

Historical checkpoint IDs remain stable. The benchmark changes how evidence is earned rather than inventing a new business task.

| Existing checkpoint family | New accepted browser evidence |
|---|---|
| Ticket read | Open and observe the locked ticket page |
| `PRE_CONFIG` | Correct configuration query from the Configuration Center |
| Five pre-change monitor checks | Exact query plus linked screenshot for each monitor page |
| Add submission/wait | Exact blank-form completion, submit and exact-job wait |
| Creation verification | Existing independent terminal grep |
| Five post-change monitor checks | Exact query plus linked screenshot for each monitor page |
| Business verification | Exact business-script parameters, submit and exact-job wait |
| Rollback submission/wait | Exact remove parameters, submit and exact-job wait |
| Rollback verification | Existing independent absence grep |

Form-fill and navigation actions may be audited without increasing the primary result denominator. The evaluator validates the final submitted payload and linked evidence rather than awarding points merely for typing.

The existing normal and anomaly outcomes remain:

- normal: postchecks and business validation support completion;
- anomaly: post-change causal metric delay supports rollback, exact remove job and absence grep.

## 14. Isolation And Security

- Vendor assets are pinned and served locally.
- Third-party licenses and notices are retained.
- No runtime CDN or external analytics requests are allowed.
- Agent HTML and JavaScript contain no provider credentials, scenario labels, evaluator state or scores.
- Credential aliases are public; secret material remains server-side.
- Observer, audit, artifacts and score routes remain outside the agent allowlist.
- Screenshot paths are run-owned and containment-checked.
- Image hashes and query/action correlation are independently verifiable.

## 15. Acceptance Criteria

### 15.1 Product surface

- All eight agent pages render under one coherent Tabler-derived shell.
- Every sidebar destination is functional; no decorative dead links are added.
- The five monitor routes share behavior and visual conventions.
- The automation page displays structured job progress and results instead of JSON.

### 15.2 Visual necessity

- Monitor page body text contains no plotted values or semantic health verdict.
- Each monitor result contains nonblank Canvas content at the benchmark viewport.
- Screenshots visibly include titles, axes, units, legends, thresholds and relevant time markers.
- Normal and anomaly post-change runtime/alarm screenshots differ materially.
- Probe, capacity and traffic remain plausibly healthy in the anomaly scenario.

### 15.3 Parameter necessity

- Correct monitor and automation values are absent from initial form values.
- Missing or incorrect automation parameters cannot create an accepted job.
- Correct ordered parameters create the same trusted add, business or remove state transitions as today.
- The actual credential is never visible or model-entered.

### 15.4 Evidence and grading

- Each required monitor checkpoint has a correct query and linked screenshot revision.
- Stale, cross-page and cross-run screenshots are rejected.
- Screenshot image blocks are actually delivered to the configured multimodal model transport.
- Existing DAG order, exact-job wait, SOP decision and terminal grep gates remain fail-closed.
- Both normal completion and anomaly rollback pass independent bundle verification.

### 15.5 Operational constraints

- The benchmark starts with the existing service command.
- No Docker daemon, additional database or external metric service is required.
- Runtime works without internet access after dependencies are vendored.

## 16. Delivery Phases

### Phase 1: minimal visual benchmark

- Vendor pinned Tabler and ECharts distributions.
- Introduce the shared shell and eight routes.
- Add deterministic chart presentation fixtures.
- Replace raw monitor JSON with Canvas charts.
- Add blank configuration and automation forms with exact validation.
- Add screenshot capture, multimodal delivery and linked evidence.
- Run normal and anomaly acceptance.

### Phase 2: optional breadth after evidence

Only if Phase 1 is stable and additional validation tickets exist:

- add more change cases using the same page contracts;
- add additional deterministic distractor services;
- add richer job-detail drill-down;
- consider aligning the symbolic browser interface with BrowserGym conventions.

Phase 2 must not delay or enlarge the first usable visual benchmark.

## 17. Deferred Implementation Decisions

The following are intentionally deferred until concurrent project changes settle:

- exact Python module names for chart fixture generation;
- whether monitor routes use one route with a page parameter or five named handlers;
- exact public projection class decomposition;
- test-file placement and migration strategy;
- whether existing page templates are incrementally adapted or replaced behind the same contracts.

These choices may change without changing the approved product, visual-evidence or evaluation contracts in this specification.
