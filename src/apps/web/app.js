const batchGrid = document.getElementById("batch-grid");
const statusText = document.getElementById("status-text");
const searchInput = document.getElementById("search-input");
const statusSelect = document.getElementById("status-select");
const sortSelect = document.getElementById("sort-select");
const refreshButton = document.getElementById("refresh-button");
const detailOverlay = document.getElementById("batch-detail-overlay");
const detailPanel = document.getElementById("batch-detail-panel");
const detailTitle = document.getElementById("detail-title");
const detailSubtitle = document.getElementById("detail-subtitle");
const detailCloseButton = document.getElementById("detail-close-button");
const detailBody = document.getElementById("detail-body");

let selectedBatchId = "";
let selectedBatchSummary = null;
let selectedTaskId = "";
let selectedTaskTimelineItems = [];
let detailAbortController = null;
let taskTimelineAbortController = null;

function formatDate(value) {
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

function hasObjectContent(value) {
  return value && typeof value === "object" && Object.keys(value).length > 0;
}

function formatJson(value) {
  if (!hasObjectContent(value)) {
    return "";
  }
  return escapeHtml(JSON.stringify(value, null, 2));
}

function renderEmpty(message) {
  batchGrid.innerHTML = `<article class="empty-state">${escapeHtml(message)}</article>`;
}

function renderBatches(items) {
  if (!items.length) {
    renderEmpty("No batches matched the current filters.");
    return;
  }

  batchGrid.innerHTML = items
    .map(
      (item) => `
        <article class="batch-card">
          <div class="card-top">
            <div>
              <h2>${escapeHtml(item.title)}</h2>
              <p class="batch-id">${escapeHtml(item.batch_id)}</p>
            </div>
            <span class="status-badge status-${escapeHtml(item.derived_status)}">${escapeHtml(item.derived_status)}</span>
          </div>
          <dl class="metrics">
            <div><dt>Total tasks</dt><dd>${escapeHtml(item.total_tasks)}</dd></div>
            <div><dt>Success rate</dt><dd>${escapeHtml(item.success_rate)}%</dd></div>
            <div><dt>Completed</dt><dd>${escapeHtml(item.completed_count)}</dd></div>
            <div><dt>Success</dt><dd>${escapeHtml(item.success_count)}</dd></div>
            <div><dt>Failed</dt><dd>${escapeHtml(item.failed_count)}</dd></div>
            <div><dt>Cancelled</dt><dd>${escapeHtml(item.cancelled_count)}</dd></div>
          </dl>
          <div class="timestamps">
            <p><strong>Created:</strong> ${escapeHtml(formatDate(item.created_at))}</p>
            <p><strong>Updated:</strong> ${escapeHtml(formatDate(item.updated_at))}</p>
          </div>
          <div class="card-actions">
            <button class="detail-link" type="button" data-batch-id="${escapeHtml(item.batch_id)}">View detail</button>
          </div>
        </article>
      `,
    )
    .join("");
}

function setPanelOpen(open) {
  detailOverlay.hidden = !open;
  detailPanel.classList.toggle("open", open);
  detailPanel.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("detail-panel-open", open);
}

function abortDetailRequests() {
  if (detailAbortController) {
    detailAbortController.abort();
    detailAbortController = null;
  }
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
    taskTimelineAbortController = null;
  }
}

function renderDetailLoading(batchId) {
  detailTitle.textContent = "Loading batch detail";
  detailSubtitle.textContent = `Batch ${batchId}`;
  detailBody.innerHTML = `
    <article class="detail-state">
      Loading tasks, workflow trajectory, and deliverables...
    </article>
  `;
}

function renderDetailError(message) {
  detailBody.innerHTML = `<article class="detail-state error">${escapeHtml(message)}</article>`;
}

function closeBatchDetail() {
  abortDetailRequests();
  selectedBatchId = "";
  selectedBatchSummary = null;
  selectedTaskId = "";
  selectedTaskTimelineItems = [];
  setPanelOpen(false);
}

function openBatchDetail(batchId) {
  selectedBatchId = batchId;
  selectedBatchSummary = null;
  selectedTaskId = "";
  selectedTaskTimelineItems = [];
  setPanelOpen(true);
  renderDetailLoading(batchId);
  loadBatchSummary(batchId);
}

async function loadBatchSummary(batchId) {
  if (detailAbortController) {
    detailAbortController.abort();
  }
  detailAbortController = new AbortController();

  try {
    const response = await fetch(`/task-batches/${batchId}/summary`, {
      signal: detailAbortController.signal,
    });
    if (response.status === 404) {
      throw new Error("Batch not found.");
    }
    if (!response.ok) {
      throw new Error(`Summary request failed with status ${response.status}`);
    }

    const summary = await response.json();
    if (selectedBatchId !== batchId) {
      return;
    }
    selectedBatchSummary = summary;
    const firstTask = summary.tasks?.[0];
    selectedTaskId = firstTask?.task_id || "";
    selectedTaskTimelineItems = [];
    renderBatchDetail(summary);
    if (firstTask) {
      loadSelectedTaskTimeline(firstTask);
    }
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    if (selectedBatchId === batchId) {
      renderDetailError(error.message || "Unable to load batch detail.");
    }
  }
}

function selectedTask() {
  return selectedBatchSummary?.tasks?.find((task) => task.task_id === selectedTaskId) || null;
}

function renderBatchDetail(summary) {
  detailTitle.textContent = summary.batch.title;
  detailSubtitle.textContent = `${summary.derived_status} / ${summary.progress.completed_count} of ${summary.progress.total_tasks} complete`;
  detailBody.innerHTML = `
    <section class="detail-layout">
      <aside class="detail-task-list" aria-label="Batch tasks">
        ${renderTaskList(summary.tasks || [])}
      </aside>
      <section class="detail-main">
        <section class="flow-section">
          <div class="detail-section-heading">
            <p class="eyebrow">Task flow</p>
            <h3>${escapeHtml(selectedTask()?.title || "No task selected")}</h3>
          </div>
          <div id="detail-flow" class="flow-timeline">
            ${renderTaskTimeline(selectedTask(), selectedTaskTimelineItems)}
          </div>
        </section>
        <section class="delivery-section">
          <div class="detail-section-heading">
            <p class="eyebrow">Deliverables</p>
            <h3>${escapeHtml(selectedTaskId ? "Selected task outputs" : "Batch outputs")}</h3>
          </div>
          <div id="detail-deliverables" class="delivery-list">
            ${renderArtifacts(summary, selectedTaskId)}
          </div>
        </section>
      </section>
    </section>
  `;
}

function renderTaskList(tasks) {
  if (!tasks.length) {
    return `<article class="detail-state">No tasks in this batch.</article>`;
  }

  return tasks
    .map(
      (task) => `
        <button
          class="detail-task-item${task.task_id === selectedTaskId ? " selected" : ""}"
          type="button"
          data-task-id="${escapeHtml(task.task_id)}"
          aria-pressed="${task.task_id === selectedTaskId ? "true" : "false"}"
        >
          <span>
            <strong>${escapeHtml(task.title)}</strong>
            <small>${escapeHtml(task.task_type)} / ${escapeHtml(task.task_id)}</small>
          </span>
          <span class="status-badge status-${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
          <span class="artifact-count">${escapeHtml(task.artifact_count)} deliverable${task.artifact_count === 1 ? "" : "s"}</span>
        </button>
      `,
    )
    .join("");
}

function selectDetailTask(taskId) {
  if (!selectedBatchSummary) {
    return;
  }
  const task = selectedBatchSummary.tasks.find((item) => item.task_id === taskId);
  if (!task) {
    return;
  }
  selectedTaskId = task.task_id;
  selectedTaskTimelineItems = [];
  renderBatchDetail(selectedBatchSummary);
  loadSelectedTaskTimeline(task);
}

async function loadSelectedTaskTimeline(task) {
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
  }
  taskTimelineAbortController = new AbortController();
  const flow = document.getElementById("detail-flow");
  if (flow) {
    flow.innerHTML = `<article class="detail-state">Loading ${escapeHtml(task.title)} flow...</article>`;
  }

  try {
    const response = await fetch(`/tasks/${task.task_id}/timeline`, {
      signal: taskTimelineAbortController.signal,
    });
    if (response.status === 404) {
      throw new Error("Task timeline not found.");
    }
    if (!response.ok) {
      throw new Error(`Task timeline request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (selectedTaskId !== task.task_id) {
      return;
    }
    selectedTaskTimelineItems = payload.items || [];
    const currentFlow = document.getElementById("detail-flow");
    if (currentFlow) {
      currentFlow.innerHTML = renderTaskTimeline(task, selectedTaskTimelineItems);
    }
  } catch (error) {
    if (error.name === "AbortError" || selectedTaskId !== task.task_id) {
      return;
    }
    const currentFlow = document.getElementById("detail-flow");
    if (currentFlow) {
      currentFlow.innerHTML = `<article class="detail-state error">${escapeHtml(error.message || "Unable to load task flow.")}</article>`;
    }
  }
}

function flowStageLabel(stage) {
  const labels = {
    created: "Created",
    routed: "Routed",
    queued: "Queued",
    blocked: "Blocked",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    review: "Review",
    retry: "Retry",
    status: "Status",
  };
  return labels[stage] || stage;
}

function renderTaskTimeline(task, items) {
  if (!task) {
    return `<article class="detail-state">No task selected.</article>`;
  }
  if (!items.length) {
    return `<article class="detail-state">No flow events available yet.</article>`;
  }

  return items
    .map(
      (item) => `
        <article class="flow-step ${escapeHtml(item.stage || "status")}">
          <div class="flow-marker"></div>
          <div class="flow-card">
            <div class="flow-card-head">
              <strong>${escapeHtml(flowStageLabel(item.stage))}</strong>
              <span>${escapeHtml(formatDate(item.timestamp))}</span>
            </div>
            <h4>${escapeHtml(item.title)}</h4>
            <p>${escapeHtml(item.detail || "No additional detail.")}</p>
            <div class="flow-meta">
              <span>actor ${escapeHtml(item.actor || "system")}</span>
              <span>run ${escapeHtml(item.run_id || "n/a")}</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderArtifacts(summary, taskId) {
  const allArtifacts = summary.artifacts || [];
  const artifacts = taskId
    ? allArtifacts.filter((artifact) => artifact.task_id === taskId)
    : allArtifacts;

  if (!artifacts.length) {
    const otherCount = taskId ? allArtifacts.filter((artifact) => artifact.task_id !== taskId).length : 0;
    return `
      <article class="detail-state">
        ${taskId ? "No deliverables for the selected task." : "No deliverables for this batch yet."}
        ${otherCount ? ` ${otherCount} deliverable${otherCount === 1 ? "" : "s"} exist on other tasks.` : ""}
      </article>
    `;
  }

  return artifacts.map((artifact) => formatArtifactPreview(artifact)).join("");
}

function formatArtifactPreview(artifact) {
  const summary = formatJson(artifact.summary);
  const structuredOutput = formatJson(artifact.structured_output);
  const rawContent = formatJson(artifact.raw_content);

  return `
    <article class="delivery-card">
      <div class="delivery-card-head">
        <strong>${escapeHtml(artifact.artifact_type)}</strong>
        <span>${escapeHtml(artifact.content_type || "unknown")}</span>
      </div>
      <p class="delivery-uri">${escapeHtml(artifact.uri)}</p>
      <p class="delivery-meta">
        created ${escapeHtml(formatDate(artifact.created_at))}
        / task ${escapeHtml(artifact.task_id || "n/a")}
        / run ${escapeHtml(artifact.run_id || "n/a")}
      </p>
      <section class="delivery-preview">
        <strong>Summary</strong>
        ${summary ? `<pre>${summary}</pre>` : "<p>No summary.</p>"}
      </section>
      <section class="delivery-preview">
        <strong>Structured output</strong>
        ${structuredOutput ? `<pre>${structuredOutput}</pre>` : "<p>No structured output.</p>"}
      </section>
      <details class="delivery-preview">
        <summary>Raw content</summary>
        ${rawContent ? `<pre>${rawContent}</pre>` : "<p>No raw content.</p>"}
      </details>
    </article>
  `;
}

async function loadBatches() {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) {
    params.set("search", searchInput.value.trim());
  }
  if (statusSelect.value) {
    params.set("status", statusSelect.value);
  }
  params.set("sort", sortSelect.value);

  statusText.textContent = "Loading batches...";
  try {
    const response = await fetch(`/task-batches?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    statusText.textContent = `${payload.items.length} batch${payload.items.length === 1 ? "" : "es"} shown`;
    renderBatches(payload.items);
  } catch (error) {
    statusText.textContent = "Unable to load batches.";
    renderEmpty(error.message);
  }
}

batchGrid.addEventListener("click", (event) => {
  const detailButton = event.target.closest("[data-batch-id]");
  if (detailButton) {
    openBatchDetail(detailButton.dataset.batchId);
  }
});

detailBody.addEventListener("click", (event) => {
  const taskButton = event.target.closest("[data-task-id]");
  if (taskButton) {
    selectDetailTask(taskButton.dataset.taskId);
  }
});

detailCloseButton.addEventListener("click", closeBatchDetail);
detailOverlay.addEventListener("click", closeBatchDetail);

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && detailPanel.classList.contains("open")) {
    closeBatchDetail();
  }
});

searchInput.addEventListener("input", () => {
  loadBatches();
});
statusSelect.addEventListener("change", () => {
  loadBatches();
});
sortSelect.addEventListener("change", () => {
  loadBatches();
});
refreshButton.addEventListener("click", () => {
  loadBatches();
});

loadBatches();
