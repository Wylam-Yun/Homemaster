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
      projected[key] = item && typeof item === "object" ? safeObjectText(item) : item;
    });
    return JSON.stringify(projected, null, 2);
  };
  const statusValue = (status) => closedStatuses.has(status) ? status : "anomaly";
  const showStatus = (status, label) => {
    const value = statusValue(status);
    const target = node("environment-result-status");
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
  const renderPlanItem = (entry) => {
    const item = document.createElement("li");
    item.dataset.status = entry.status;
    item.textContent = `${entry.title} · ${entry.status}`;
    return item;
  };
  const renderPlan = (plan) => {
    const items = (plan && plan.items) || [];
    const active = items.find((entry) => entry.id === (plan && plan.current_id));
    setText("plan-active", active && active.title, "等待模型创建计划");
    setText("plan-current", plan && plan.current_id);
    setText("plan-next", plan && plan.next_focus);
    const rows = items
      .filter((entry) => !active || entry.id !== active.id)
      .map(renderPlanItem);
    node("model-plan").replaceChildren(...rows);
  };
  const renderModelOutput = (action, publicOutput) => {
    const kinds = [];
    if (action) kinds.push("tool_call");
    if (publicOutput) kinds.push(`${publicOutput.kind}/${publicOutput.outcome}`);
    setText("model-output-kind", kinds.join(" · "), "等待模型输出");
    setText("model-output-tool", action && action.tool_name, "等待模型选择工具");
    setText("model-output-tool-label", action && action.tool_label_zh);
    setText("model-output-tool-kind", action && action.tool_kind);
    setText("model-output-arguments", safeObjectText(action && action.arguments));
    setText("public-model-reply", publicOutput && publicOutput.text);
  };
  const renderEnvironmentResult = (result) => {
    if (!result) {
      showStatus("running", "等待结果");
      setText("environment-result-summary", "尚无执行结果");
      setText("environment-result-failure", "—");
      return;
    }
    showStatus(result.status);
    setText("environment-result-summary", safeObjectText(result.result), "执行状态已更新");
    setText("environment-result-failure", result.failure_code);
  };
  const renderSummaryTerm = (term) => {
    if (!term) return "—";
    const values = safeObjectText(term.values);
    return values === "—" ? term.label_zh : `${term.label_zh} · ${values}`;
  };
  const renderDecisionSummary = (summary) => {
    setText("decision-fact", renderSummaryTerm(summary && summary.fact));
    setText("decision-judgment", renderSummaryTerm(summary && summary.judgment));
    setText("decision-next", renderSummaryTerm(summary && summary.next_action));
  };
  const renderResolvedIncident = (entry) => {
    const item = document.createElement("li");
    item.dataset.status = "resolved";
    item.textContent = `${entry.label_zh} · ${entry.recovery.tool_name}`;
    return item;
  };
  const renderHistoryEntry = (entry) => {
    const item = document.createElement("li");
    item.dataset.kind = entry.kind;
    item.textContent = `${entry.label_zh} · ${entry.status}`;
    return item;
  };
  const renderIncidents = (incidents) => {
    const all = incidents || [];
    const open = [...all].reverse().find((entry) => entry.status === "open");
    const target = node("open-incident");
    target.dataset.status = open ? "open" : "clear";
    target.textContent = open
      ? `${open.failure_code} · ${open.label_zh} · ${open.failed_tool} · ${safeObjectText(open.target)}`
      : "当前无未恢复异常";
    const resolved = all.filter((entry) => entry.status === "resolved").slice(-2);
    node("resolved-incidents").replaceChildren(...resolved.map(renderResolvedIncident));
  };
  const renderCriticalHistory = (history) => {
    const latest = (history || []).slice(-4);
    node("critical-history").replaceChildren(...latest.map(renderHistoryEntry));
  };
  const clearDynamicState = () => {
    setText("current-stage", "等待开始");
    renderStages("");
    renderTask(null);
    renderPlan(null);
    renderModelOutput(null, null);
    renderEnvironmentResult(null);
    renderDecisionSummary(null);
    renderIncidents([]);
    renderCriticalHistory([]);
    setText("run-outcome", "进行中");
    setText("score-summary", "等待评估");
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
    renderTask(snapshot.current_task);
    renderPlan(snapshot.plan);
    renderModelOutput(snapshot.current_action, snapshot.public_model_output);
    renderEnvironmentResult(snapshot.last_result);
    renderDecisionSummary(snapshot.decision_summary);
    renderIncidents(snapshot.incidents);
    renderCriticalHistory(snapshot.critical_history);
    setText("current-stage", stageLabel(snapshot.stage));
    renderStages(snapshot.stage);
    setText("run-outcome", snapshot.terminal_outcome, "进行中");
    return true;
  };
  const applyEvent = (event) => {
    if (!Number.isFinite(event.sequence) || event.sequence <= lastSequence) return false;
    lastSequence = event.sequence;
    if (event.event_type === "tool.call_started") renderModelOutput(event, null);
    if (["accepted", "succeeded", "failed", "rejected"].includes(event.status)) {
      renderEnvironmentResult(event);
    }
    if (event.decision_summary) renderDecisionSummary(event.decision_summary);
    return true;
  };
  const parseData = (message, apply) => {
    try {
      apply(JSON.parse(message.data));
    } catch (_error) {
      showStatus("anomaly", "数据不可用");
      setText("environment-result-summary", "等待下一条安全展示事件");
    }
  };
  const refreshSnapshot = () => fetch(`/api/runs/${runId}/presentation`)
    .then((response) => {
      if (!response.ok) throw new Error("snapshot unavailable");
      return response.json();
    })
    .then((payload) => applySnapshot(payload.snapshot))
    .catch(() => false);
  const formatScore = (value) => value === null || value === undefined
    ? "pending"
    : Number(value).toFixed(1);
  const formatState = (value) => value === null || value === undefined
    ? "pending"
    : String(value);
  const refreshScores = () => fetch(`/api/runs/${runId}/scores`)
    .then((response) => {
      if (!response.ok) throw new Error("scores unavailable");
      return response.json();
    })
    .then((payload) => {
      if (payload.status !== "final" || !payload.summary) return false;
      const summary = payload.summary;
      setText(
        "score-summary",
        `trajectory=${formatScore(summary.trajectory_score)} result=${formatScore(summary.result_score)} video_verification=${formatState(summary.video_verification)} formal_success=${formatState(summary.formal_success)}`,
      );
      return true;
    })
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
      showStatus("anomaly", "正在重连");
      setText("environment-result-summary", "实时展示流正在重连");
    };
  };
  refreshSnapshot().finally(() => {
    connect();
    refreshScores();
    window.setInterval(refreshScores, 400);
  });
})();
