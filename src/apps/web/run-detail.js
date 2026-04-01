const runTitle = document.getElementById("run-title");
const runSubtitle = document.getElementById("run-subtitle");
const statusText = document.getElementById("status-text");
const overviewHeading = document.getElementById("overview-heading");
const overviewStatus = document.getElementById("overview-status");
const overviewMetrics = document.getElementById("overview-metrics");
const routingPanel = document.getElementById("routing-panel");
const inputSnapshot = document.getElementById("input-snapshot");
const outputSnapshot = document.getElementById("output-snapshot");
const errorPanel = document.getElementById("error-panel");
const logList = document.getElementById("log-list");
const retryHistory = document.getElementById("retry-history");
const eventList = document.getElementById("event-list");
const backToBatch = document.getElementById("back-to-batch");

function formatDate(value) {
  if (!value) {
    return "n/a";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function runIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function prettyJson(value) {
  if (!value || !Object.keys(value).length) {
    return "No data";
  }
  return JSON.stringify(value, null, 2);
}

function renderOverview(detail) {
  runTitle.textContent = detail.task.title;
  runSubtitle.textContent = `Run ${detail.run.id} for task ${detail.task.task_id}`;
  statusText.textContent = `Run ${detail.run.id} is ${detail.run.run_status}.`;
  overviewHeading.textContent = `${detail.task.task_type} · ${detail.routing.agent_role_name ?? detail.task.assigned_agent_role ?? "unassigned"}`;
  overviewStatus.textContent = detail.run.run_status;
  overviewStatus.className = `status-badge status-${detail.run.run_status}`;
  backToBatch.href = `/console/batches/${detail.task.batch_id}`;

  const metrics = [
    ["Task status", detail.task.status],
    ["Retry count", detail.task.retry_count],
    ["Started", formatDate(detail.run.started_at)],
    ["Finished", formatDate(detail.run.finished_at)],
    ["Latency", detail.run.latency_ms ?? "n/a"],
    ["Prompt tokens", detail.run.token_usage?.prompt_tokens ?? 0],
    ["Completion tokens", detail.run.token_usage?.completion_tokens ?? 0],
    ["Total tokens", detail.run.token_usage?.total_tokens ?? 0],
  ];
  overviewMetrics.innerHTML = metrics
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
}

function renderRouting(detail) {
  routingPanel.innerHTML = `
    <article class="detail-entry">
      <strong>Routing reason</strong>
      <p>${escapeHtml(detail.routing.routing_reason ?? "No routing reason recorded.")}</p>
    </article>
    <article class="detail-entry">
      <strong>Agent role</strong>
      <p>${escapeHtml(detail.routing.agent_role_name ?? detail.task.assigned_agent_role ?? "unassigned")}</p>
      <p class="muted">${escapeHtml(detail.routing.agent_role_id ?? "role id unavailable")}</p>
    </article>
    <article class="detail-entry">
      <strong>Task context</strong>
      <p>${escapeHtml(detail.task.task_id)}</p>
      <p class="muted">${escapeHtml(detail.task.task_type)}</p>
    </article>
  `;
}

function renderSnapshots(detail) {
  inputSnapshot.textContent = prettyJson(detail.run.input_snapshot);
  outputSnapshot.textContent = prettyJson(detail.run.output_snapshot);
}

function renderErrorAndLogs(detail) {
  const errorClass = detail.run.run_status === "cancelled" ? "cancelled" : "failed";
  if (detail.run.error_message || detail.run.cancel_reason) {
    errorPanel.innerHTML = `
      <article class="error-entry ${errorClass}">
        <strong>${detail.run.run_status === "cancelled" ? "Cancel context" : "Error message"}</strong>
        <p>${escapeHtml(detail.run.error_message ?? detail.run.cancel_reason)}</p>
      </article>
    `;
  } else {
    errorPanel.innerHTML = `<div class="empty-panel">No error or cancellation context for this run.</div>`;
  }

  if (!detail.run.logs.length) {
    logList.innerHTML = `<div class="empty-panel">This run does not have execution logs.</div>`;
    return;
  }

  logList.innerHTML = detail.run.logs
    .map(
      (log, index) => `
        <article class="log-entry">
          <p class="log-meta">log ${index + 1}</p>
          <p>${escapeHtml(log)}</p>
        </article>
      `,
    )
    .join("");
}

function renderRetryHistory(detail) {
  if (!detail.retry_history.length) {
    retryHistory.innerHTML = `<div class="empty-panel">No retry history available.</div>`;
    return;
  }

  retryHistory.innerHTML = detail.retry_history
    .map(
      (item) => `
        <article class="history-entry ${item.is_current ? "current" : ""}">
          <strong>${item.is_current ? "Current run" : "Previous run"}</strong>
          <p>${escapeHtml(item.run_id)}</p>
          <p class="history-meta">${escapeHtml(item.run_status)} · started ${escapeHtml(formatDate(item.started_at))}</p>
          <p class="history-meta">latency ${escapeHtml(item.latency_ms ?? "n/a")} ms</p>
          ${item.error_message ? `<p>${escapeHtml(item.error_message)}</p>` : ""}
        </article>
      `,
    )
    .join("");
}

function renderEvents(detail) {
  if (!detail.events.length) {
    eventList.innerHTML = `<article class="empty-state">No task events available.</article>`;
    return;
  }

  eventList.innerHTML = detail.events
    .map(
      (event) => `
        <article class="event-entry">
          <strong>${escapeHtml(event.event_type)}</strong>
          <p>${escapeHtml(event.message ?? "No message")}</p>
          <p class="event-meta">${escapeHtml(event.event_status ?? "no status")} · ${escapeHtml(formatDate(event.created_at))}</p>
        </article>
      `,
    )
    .join("");
}

function renderError(message) {
  statusText.textContent = message;
  routingPanel.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  inputSnapshot.textContent = message;
  outputSnapshot.textContent = message;
  errorPanel.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  logList.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  retryHistory.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  eventList.innerHTML = `<article class="empty-state">${escapeHtml(message)}</article>`;
}

async function loadRunDetail() {
  const runId = runIdFromPath();
  if (!runId) {
    renderError("Run id is missing from the URL.");
    return;
  }

  try {
    const response = await fetch(`/runs/${runId}/detail`);
    if (response.status === 404) {
      throw new Error("Run not found.");
    }
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const detail = await response.json();
    renderOverview(detail);
    renderRouting(detail);
    renderSnapshots(detail);
    renderErrorAndLogs(detail);
    renderRetryHistory(detail);
    renderEvents(detail);
  } catch (error) {
    renderError(error.message);
  }
}

loadRunDetail();
