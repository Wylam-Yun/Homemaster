"use strict";
(() => {
  const runId = window.OBSERVER_RUN_ID;
  const stages = [
    ["check_before_change", "变更前检查"],
    ["change_implement", "变更执行"],
    ["implementation_verify", "独立验证"],
    ["change_verified", "变更后检查"],
    ["business_verify", "业务验证"],
    ["change_rollback", "回滚"],
    ["terminal", "完成"],
  ];
  const closedStatuses = new Set([
    "running",
    "accepted",
    "succeeded",
    "failed",
    "rejected",
    "anomaly",
  ]);
  const completedKeys = new Set();
  let presentationGeneration = null;
  let lastSequence = 0;

  const node = (id) => document.getElementById(id);
  const setText = (id, value, fallback = "—") => {
    node(id).textContent = value === null || value === undefined || value === ""
      ? fallback
      : String(value);
  };
  const safeObjectText = (value) => {
    if (value === null || value === undefined) return "—";
    if (typeof value !== "object") return String(value);
    const keys = Object.keys(value).sort();
    if (keys.length === 0) return "—";
    const projected = {};
    keys.forEach((key) => {
      const item = value[key];
      projected[key] = item && typeof item === "object"
        ? safeObjectText(item)
        : item;
    });
    return JSON.stringify(projected, null, 2);
  };
  const statusValue = (status) => closedStatuses.has(status) ? status : "anomaly";
  const showStatus = (status, label) => {
    const value = statusValue(status);
    const target = node("latest-result-status");
    target.dataset.status = value;
    target.className = `observer-status status-${value}`;
    target.textContent = label || value.toUpperCase();
  };
  const stageLabel = (stage) => {
    const match = stages.find(([key]) => key === stage);
    return match ? match[1] : "等待开始";
  };
  const renderStages = (currentStage) => {
    const activeIndex = stages.findIndex(([key]) => key === currentStage);
    node("stage-list").querySelectorAll("li").forEach((item, index) => {
      item.classList.toggle("active", index === activeIndex);
      item.classList.toggle("completed", activeIndex >= 0 && index < activeIndex);
      item.setAttribute("aria-current", index === activeIndex ? "step" : "false");
    });
  };
  const renderTask = (task) => {
    setText("current-sop-name", task && task.check_name, "等待 Agent 读取变更单");
    setText("current-sop-text", task && task.source_text, "等待原始 SOP 文本");
  };
  const renderCompleted = () => {
    const items = [];
    completedKeys.forEach((encoded) => {
      const item = document.createElement("li");
      item.textContent = encoded.split("\u0000", 2)[1] || encoded;
      items.push(item);
    });
    node("completed-steps").replaceChildren(...items);
  };
  const addCompleted = (event) => {
    if (event.status === "succeeded" && !event.failure && event.task) {
      const key = event.task.source_sha256 || event.task.check_name;
      completedKeys.add(`${key}\u0000${event.task.check_name}`);
      renderCompleted();
    }
  };
  const renderEvent = (event) => {
    if (!event) return;
    setText("current-stage", stageLabel(event.stage));
    renderStages(event.stage);
    if (event.task) renderTask(event.task);
    setText("current-tool-name", event.tool_name, "等待 Agent 操作");
    setText("current-tool-arguments", safeObjectText(event.arguments));
    showStatus(event.failure ? "failed" : event.status);
    if (event.failure) {
      setText("latest-result-summary", "Action failed; successful output was not recorded.");
    } else {
      setText("latest-result-summary", safeObjectText(event.result), "执行状态已更新");
    }
    setText("latest-result-evidence", (event.evidence_refs || []).join(" · "));
    addCompleted(event);
  };
  const clearDynamicState = () => {
    completedKeys.clear();
    renderCompleted();
    setText("current-stage", "等待开始");
    renderStages("");
    renderTask(null);
    setText("current-tool-name", "等待 Agent 操作");
    setText("current-tool-arguments", "—");
    showStatus("running", "等待结果");
    setText("latest-result-summary", "尚无执行结果");
    setText("latest-result-evidence", "—");
    setText("next-step", "等待 Agent 读取变更单");
    setText("run-outcome", "IN PROGRESS");
    setText("score-summary", "PENDING EVALUATION");
  };
  const applySnapshot = (snapshot) => {
    const generation = Number(snapshot.presentation_generation || 0);
    const snapshotSequence = Number(snapshot.last_sequence || 0);
    if (!Number.isFinite(generation) || !Number.isFinite(snapshotSequence)) return false;
    if (presentationGeneration !== null && generation < presentationGeneration) return false;
    if (
      presentationGeneration !== null
      && generation === presentationGeneration
      && snapshotSequence < lastSequence
    ) return false;
    if (presentationGeneration !== null && generation > presentationGeneration) {
      lastSequence = 0;
      clearDynamicState();
    }
    presentationGeneration = generation;
    lastSequence = snapshotSequence;
    completedKeys.clear();
    (snapshot.completed_steps || []).forEach((task) => {
      const key = task.source_sha256 || task.check_name;
      completedKeys.add(`${key}\u0000${task.check_name}`);
    });
    renderCompleted();
    renderTask(snapshot.current_task);
    if (snapshot.last_event) renderEvent(snapshot.last_event);
    setText("current-stage", stageLabel(snapshot.stage));
    renderStages(snapshot.stage);
    setText("next-step", snapshot.next_step, "等待 Agent 读取变更单");
    setText("run-outcome", snapshot.terminal_outcome, "IN PROGRESS");
    const score = snapshot.last_event && snapshot.last_event.result
      ? snapshot.last_event.result.score_summary
      : null;
    setText("score-summary", safeObjectText(score), "PENDING EVALUATION");
    return true;
  };
  const applyEvent = (event) => {
    if (!Number.isFinite(event.sequence) || event.sequence <= lastSequence) return false;
    lastSequence = event.sequence;
    renderEvent(event);
    return true;
  };
  const parseData = (message, apply) => {
    try {
      apply(JSON.parse(message.data));
    } catch (_error) {
      showStatus("anomaly", "STREAM DATA UNAVAILABLE");
      setText("latest-result-summary", "Waiting for the next safe presentation update.");
    }
  };
  const refreshSnapshot = () => fetch(`/api/runs/${runId}/presentation`)
    .then((response) => {
      if (!response.ok) throw new Error("snapshot unavailable");
      return response.json();
    })
    .then((payload) => applySnapshot(payload.snapshot))
    .catch(() => false);
  const connect = () => {
    const stream = new EventSource(`/api/runs/${runId}/presentation-events`);
    stream.addEventListener("presentation.snapshot", (message) => {
      parseData(message, applySnapshot);
    });
    stream.addEventListener("presentation.event", (message) => {
      parseData(message, (event) => {
        if (applyEvent(event)) refreshSnapshot();
      });
    });
    stream.onerror = () => {
      showStatus("anomaly", "RECONNECTING");
      setText("latest-result-summary", "Live presentation stream reconnecting…");
    };
  };
  refreshSnapshot().finally(connect);
})();
