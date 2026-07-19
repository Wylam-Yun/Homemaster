"use strict";
(() => {
  const view = window.RUN_PUBLIC;
  const receipt = document.querySelector("#automation-receipt");
  const body = document.querySelector("#jobs-body");
  const submit = document.querySelector("#automation-submit");
  function takeAction() {
    const action = window.__coworkerAction;
    window.__coworkerAction = null;
    if (!action || !action.id) throw new Error("This action must be dispatched by the run driver.");
    return action;
  }
  function value(id) { return document.querySelector(`#${id}`).value; }
  function parameters(operation) {
    if (operation === "add") return {TenantId: value("automation-tenant-id"), ItemCode: value("automation-item-code"), SpecCode: value("automation-spec-code"), ExtensionName: value("automation-extension-name")};
    if (operation === "remove") return {TenantId: value("automation-tenant-id"), ItemCode: value("automation-item-code")};
    return {resource_bucket: value("automation-resource-bucket"), business_timestamp: value("automation-business-timestamp"), factor: value("automation-factor")};
  }
  async function poll(jobId, row) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const response = await fetch(`/api/runs/${view.run_id}/automation/jobs/${jobId}`);
      const payload = await response.json();
      if (!response.ok || !payload.success) throw new Error(payload.message || "Job lookup failed");
      view.state_version = payload.state_version;
      row.querySelector(".job-status").textContent = payload.job.status;
      row.children[3].textContent = payload.job.business_return_code ?? "";
      row.dataset.jobStatus = payload.job.status;
      if (["succeeded", "failed"].includes(payload.job.status)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("Job polling timed out");
  }
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const action = takeAction();
      const operation = value("automation-operation");
      const response = await fetch(`/api/runs/${view.run_id}/automation/jobs`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action_id: action.id, page_state_version: action.version, script: value("automation-script"), operation, parameters: parameters(operation)}),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) throw new Error(payload.message || "Submission failed");
      view.state_version = payload.page_state_version;
      const job = payload.visible_observation;
      const row = document.createElement("tr");
      row.dataset.jobId = job.job_id;
      row.innerHTML = `<td>${job.job_id}</td><td>${job.operation}</td><td class="job-status">${job.status}</td><td></td>`;
      body.append(row);
      receipt.textContent = JSON.stringify(job);
      receipt.className = "receipt success";
      receipt.dataset.evidenceRefs = payload.evidence_refs.join(",");
      poll(job.job_id, row).catch((error) => { receipt.textContent = error.message; receipt.className = "receipt error"; });
    } catch (error) {
      receipt.textContent = error.message;
      receipt.className = "receipt error";
    } finally {
      submit.disabled = false;
    }
  });
})();
