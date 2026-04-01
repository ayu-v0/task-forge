const batchTitle = document.getElementById("batch-title");
const batchSubtitle = document.getElementById("batch-subtitle");
const statusText = document.getElementById("status-text");
const overviewHeading = document.getElementById("overview-heading");
const overviewStatus = document.getElementById("overview-status");
const overviewMetrics = document.getElementById("overview-metrics");
const riskGroups = document.getElementById("risk-groups");
const dependencyMap = document.getElementById("dependency-map");
const artifactList = document.getElementById("artifact-list");
const taskGrid = document.getElementById("task-grid");

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

function batchIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function renderOverview(summary) {
  batchTitle.textContent = summary.batch.title;
  batchSubtitle.textContent = `Batch ${summary.batch.id} created ${formatDate(summary.batch.created_at)}`;
  overviewHeading.textContent = `${summary.tasks.length} tasks in this batch`;
  overviewStatus.textContent = summary.derived_status;
  overviewStatus.className = `status-badge status-${summary.derived_status}`;

  const metrics = [
    ["Derived status", summary.derived_status],
    ["Total tasks", summary.progress.total_tasks],
    ["Completed", summary.progress.completed_count],
    ["Progress", `${summary.progress.progress_percent}%`],
    ["Success", summary.counts.success_count],
    ["Failed", summary.counts.failed_count],
    ["Needs review", summary.counts.needs_review_count],
    ["Blocked", summary.counts.blocked_count],
    ["Cancelled", summary.counts.cancelled_count],
  ];
  overviewMetrics.innerHTML = metrics
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
}

function renderRiskGroups(tasks) {
  const groups = [
    {
      key: "failed",
      label: "Failed tasks",
      items: tasks.filter((task) => task.status === "failed"),
      description: "Execution failed and needs intervention.",
    },
    {
      key: "needs_review",
      label: "Needs review",
      items: tasks.filter((task) => task.status === "needs_review"),
      description: "Routing or approval is waiting for a human decision.",
    },
    {
      key: "blocked",
      label: "Blocked tasks",
      items: tasks.filter((task) => task.status === "blocked"),
      description: "Dependencies are not complete yet.",
    },
  ];

  const activeGroups = groups.filter((group) => group.items.length > 0);
  if (!activeGroups.length) {
    riskGroups.innerHTML = `<div class="empty-panel">No failed, blocked, or review-pending tasks in this batch.</div>`;
    return;
  }

  riskGroups.innerHTML = activeGroups
    .map(
      (group) => `
        <section class="risk-group ${group.key}">
          <strong>${group.label}</strong>
          <p>${group.description}</p>
          <ul>
            ${group.items.map((task) => `<li>${escapeHtml(task.title)} (${escapeHtml(task.task_id)})</li>`).join("")}
          </ul>
        </section>
      `,
    )
    .join("");
}

function renderDependencyMap(tasks) {
  const taskTitleById = new Map(tasks.map((task) => [task.task_id, task.title]));
  if (!tasks.length) {
    dependencyMap.innerHTML = `<div class="empty-panel">No tasks found.</div>`;
    return;
  }

  dependencyMap.innerHTML = tasks
    .map((task) => {
      if (!task.dependency_ids.length) {
        return `
          <article class="dependency-row">
            <div>
              <strong>${escapeHtml(task.title)}</strong>
              <p class="dependency-meta">${escapeHtml(task.task_id)}</p>
            </div>
            <p>No upstream dependency.</p>
          </article>
        `;
      }
      const dependencies = task.dependency_ids
        .map((dependencyId) => `${taskTitleById.get(dependencyId) ?? dependencyId} (${dependencyId})`)
        .map((text) => `<li>${escapeHtml(text)}</li>`)
        .join("");
      return `
        <article class="dependency-row">
          <div>
            <strong>${escapeHtml(task.title)}</strong>
            <p class="dependency-meta">${escapeHtml(task.task_id)}</p>
          </div>
          <div>
            <p>Depends on ${task.dependency_ids.length} task${task.dependency_ids.length === 1 ? "" : "s"}.</p>
            <ul class="dependency-list">${dependencies}</ul>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) {
    artifactList.innerHTML = `<div class="empty-panel">This batch has not produced artifacts yet.</div>`;
    return;
  }

  artifactList.innerHTML = artifacts
    .map(
      (artifact) => `
        <article class="artifact-row">
          <div>
            <strong>${escapeHtml(artifact.artifact_type)}</strong>
            <p class="artifact-meta">${escapeHtml(artifact.uri)}</p>
          </div>
          <div class="pill-row">
            <span class="meta-pill">task ${escapeHtml(artifact.task_id ?? "n/a")}</span>
            <span class="meta-pill">${escapeHtml(artifact.content_type ?? "unknown")}</span>
            <span class="meta-pill">${escapeHtml(formatDate(artifact.created_at))}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function taskFlags(task) {
  const flags = [];
  if (task.status === "needs_review") {
    flags.push("Manual review required");
  }
  if (task.status === "blocked" && task.dependency_ids.length) {
    flags.push(`Blocked by ${task.dependency_ids.length} dependency`);
  }
  if (task.status === "failed" && task.error_message) {
    flags.push("Latest run returned an error");
  }
  return flags;
}

function renderTasks(tasks) {
  if (!tasks.length) {
    taskGrid.innerHTML = `<article class="empty-state">No tasks available for this batch.</article>`;
    return;
  }

  taskGrid.innerHTML = tasks
    .map((task) => {
      const flags = taskFlags(task);
      const dependencyText = task.dependency_ids.length
        ? task.dependency_ids.join(", ")
        : "No dependencies";
      const outputText = Object.keys(task.output_snapshot ?? {}).length
        ? escapeHtml(JSON.stringify(task.output_snapshot, null, 2))
        : "No output snapshot";
      return `
        <article class="task-card ${escapeHtml(task.status)}">
          <div class="task-header">
            <div>
              <h3>${escapeHtml(task.title)}</h3>
              <p class="task-meta">${escapeHtml(task.task_type)} · ${escapeHtml(task.task_id)}</p>
            </div>
            <span class="status-badge status-${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
          </div>
          <div class="task-body">
            <section class="detail-block">
              <strong>Routing</strong>
              <div class="pill-row">
                <span class="meta-pill">agent ${escapeHtml(task.assigned_agent_role ?? "unassigned")}</span>
                <span class="meta-pill">latest run ${escapeHtml(task.latest_run_status ?? "not started")}</span>
                <span class="meta-pill">${escapeHtml(task.artifact_count)} artifacts</span>
              </div>
              ${
                task.latest_run_id
                  ? `<p class="detail-link-row"><a class="detail-link" href="/console/runs/${escapeHtml(task.latest_run_id)}">View run detail</a></p>`
                  : ""
              }
            </section>
            <section class="detail-block">
              <strong>Dependencies</strong>
              <p class="task-dependencies">${escapeHtml(dependencyText)}</p>
            </section>
            <section class="detail-block">
              <strong>Latest output</strong>
              <pre class="task-output">${outputText}</pre>
            </section>
            <section class="detail-block">
              <strong>Error / cancel context</strong>
              ${
                task.error_message
                  ? `<p class="task-error">${escapeHtml(task.error_message)}</p>`
                  : task.cancel_reason
                    ? `<p class="task-error">${escapeHtml(task.cancel_reason)}</p>`
                    : `<p class="task-empty">No error or cancel signal.</p>`
              }
            </section>
            ${
              flags.length
                ? `
                  <section class="detail-block">
                    <strong>Attention flags</strong>
                    <ul class="task-flags">${flags.map((flag) => `<li>${escapeHtml(flag)}</li>`).join("")}</ul>
                  </section>
                `
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function renderError(message) {
  statusText.textContent = message;
  overviewMetrics.innerHTML = "";
  riskGroups.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  dependencyMap.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  artifactList.innerHTML = `<div class="empty-panel">${escapeHtml(message)}</div>`;
  taskGrid.innerHTML = `<article class="empty-state">${escapeHtml(message)}</article>`;
}

async function loadBatchDetail() {
  const batchId = batchIdFromPath();
  if (!batchId) {
    renderError("Batch id is missing from the URL.");
    return;
  }

  statusText.textContent = "Loading batch summary...";

  try {
    const response = await fetch(`/task-batches/${batchId}/summary`);
    if (response.status === 404) {
      throw new Error("Batch not found.");
    }
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const summary = await response.json();
    statusText.textContent = `Batch ${summary.batch.id} is currently ${summary.derived_status}.`;
    renderOverview(summary);
    renderRiskGroups(summary.tasks);
    renderDependencyMap(summary.tasks);
    renderArtifacts(summary.artifacts);
    renderTasks(summary.tasks);
  } catch (error) {
    renderError(error.message);
  }
}

loadBatchDetail();
