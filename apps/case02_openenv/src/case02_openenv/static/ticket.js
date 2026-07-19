"use strict";
(() => {
  const view = window.RUN_PUBLIC;
  const receipt = document.querySelector("#ticket-receipt");
  const checks = {
    "ticket-query-extension-config": "extension_config",
    "ticket-query-upstream-ready": "upstream_ready",
  };
  function takeAction() {
    const action = window.__coworkerAction;
    window.__coworkerAction = null;
    if (!action || !action.id) throw new Error("This action must be dispatched by the run driver.");
    return action;
  }
  for (const [bid, check] of Object.entries(checks)) {
    const button = document.querySelector(`[data-bid="${bid}"]`);
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const action = takeAction();
        const response = await fetch(`/api/runs/${view.run_id}/ticket/config-check`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({action_id: action.id, page_state_version: action.version, check}),
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) throw new Error(payload.message || "Check failed");
        view.state_version = payload.page_state_version;
        receipt.textContent = JSON.stringify(payload.visible_observation);
        receipt.className = "receipt success";
        receipt.dataset.evidenceRefs = payload.evidence_refs.join(",");
      } catch (error) {
        receipt.textContent = error.message;
        receipt.className = "receipt error";
      } finally {
        button.disabled = false;
      }
    });
  }
})();
