"use strict";
(() => {
  const runId = window.OBSERVER_RUN_ID;
  async function refresh() {
    const [stateResponse, auditResponse, scoreResponse] = await Promise.all([
      fetch(`/api/runs/${runId}/state`),
      fetch(`/api/runs/${runId}/audit`),
      fetch(`/api/runs/${runId}/scores`),
    ]);
    const state = await stateResponse.json();
    const audit = await auditResponse.json();
    const scores = await scoreResponse.json();
    if (state.success) {
      document.querySelector("#observer-state").textContent = JSON.stringify(state.state, null, 2);
      document.querySelector("#observer-phase").textContent = state.state.phase;
    }
    if (audit.success) {
      document.querySelector("#observer-events").innerHTML = audit.events.slice(-40).map((event) => `<li><strong>${event.node_id || event.kind}</strong> ${event.status} <small>${event.event_id}</small></li>`).join("");
    }
    if (scores.status === "final") {
      const summary = scores.summary;
      document.querySelector("#trajectory-score").textContent = summary.trajectory_score.toFixed(1);
      document.querySelector("#result-score").textContent = summary.result_score.toFixed(1);
      document.querySelector("#overall-score").textContent = summary.overall_score.toFixed(1);
      document.querySelector("#video-status").textContent = summary.video_verification || "pending";
      document.querySelector("#formal-status").textContent = summary.formal_success === null ? "pending" : String(summary.formal_success);
    }
  }
  refresh();
  window.setInterval(refresh, 500);
})();
