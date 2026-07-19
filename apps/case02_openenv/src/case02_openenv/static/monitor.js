"use strict";
(() => {
  const view = window.RUN_PUBLIC;
  const result = document.querySelector("#monitor-result");
  const stage = document.querySelector("#monitor-stage");
  const queries = {
    "monitor-query-alarm": "alarm",
    "monitor-query-probe": "probe",
    "monitor-query-capacity": "capacity",
    "monitor-query-runtime-metrics": "runtime_metrics",
    "monitor-query-traffic": "traffic",
  };
  function takeAction() {
    const action = window.__coworkerAction;
    window.__coworkerAction = null;
    if (!action || !action.id) throw new Error("This action must be dispatched by the run driver.");
    return action;
  }
  for (const [bid, query] of Object.entries(queries)) {
    const button = document.querySelector(`[data-bid="${bid}"]`);
    button.addEventListener("click", async () => {
      button.disabled = true;
      stage.textContent = "running";
      stage.className = "status pending";
      try {
        const action = takeAction();
        const response = await fetch(`/api/runs/${view.run_id}/monitor/query`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            action_id: action.id,
            page_state_version: action.version,
            query,
            region: document.querySelector("#monitor-region").value,
            cluster: document.querySelector("#monitor-cluster").value,
          }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) throw new Error(payload.message || "Query failed");
        view.state_version = payload.page_state_version;
        result.textContent = JSON.stringify(payload.visible_observation, null, 2);
        result.className = "";
        result.dataset.evidenceRefs = payload.evidence_refs.join(",");
        const active = payload.visible_observation.status === "active";
        stage.textContent = active ? "active alarm" : "healthy";
        stage.className = active ? "status failed" : "status healthy";
      } catch (error) {
        result.textContent = error.message;
        result.className = "error";
        stage.textContent = "failed";
        stage.className = "status failed";
      } finally {
        button.disabled = false;
      }
    });
  }
})();
