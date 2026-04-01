const taskTypeInput = document.getElementById("task-type-input");
const diagnoseButton = document.getElementById("diagnose-button");
const refreshButton = document.getElementById("refresh-button");
const statusText = document.getElementById("status-text");
const summaryMetrics = document.getElementById("summary-metrics");
const diagnosisContent = document.getElementById("diagnosis-content");
const agentGrid = document.getElementById("agent-grid");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function formatPercent(value) {
  if (value === null || value === undefined) {
    return "No run history";
  }
  return `${value}%`;
}

function formatJson(value) {
  const entries = value && typeof value === "object" ? Object.keys(value) : [];
  if (!entries.length) {
    return "No schema declared";
  }
  return escapeHtml(JSON.stringify(value, null, 2));
}

function renderSummary(items) {
  const enabledCount = items.filter((item) => item.enabled).length;
  const disabledCount = items.length - enabledCount;
  const rolesWithHistory = items.filter((item) => item.total_runs > 0).length;

  const metrics = [
    ["Total roles", items.length],
    ["Enabled", enabledCount],
    ["Disabled", disabledCount],
    ["With run history", rolesWithHistory],
  ];

  summaryMetrics.innerHTML = metrics
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
}

function renderDiagnosis(diagnosis) {
  if (!diagnosis) {
    diagnosisContent.innerHTML = "Enter a task type to inspect matching roles.";
    return;
  }

  const enabled = diagnosis.matching_enabled_roles.length
    ? diagnosis.matching_enabled_roles.map((role) => `<span class="meta-pill">${escapeHtml(role)}</span>`).join("")
    : '<span class="muted">None</span>';
  const disabled = diagnosis.matching_disabled_roles.length
    ? diagnosis.matching_disabled_roles.map((role) => `<span class="meta-pill disabled">${escapeHtml(role)}</span>`).join("")
    : '<span class="muted">None</span>';

  diagnosisContent.innerHTML = `
    <article class="diagnosis-card diagnosis-${escapeHtml(diagnosis.status)}">
      <p class="diagnosis-status">${escapeHtml(diagnosis.status)}</p>
      <p class="diagnosis-message">${escapeHtml(diagnosis.message)}</p>
      <div class="diagnosis-group">
        <strong>Enabled matches</strong>
        <div class="pill-row">${enabled}</div>
      </div>
      <div class="diagnosis-group">
        <strong>Disabled matches</strong>
        <div class="pill-row">${disabled}</div>
      </div>
    </article>
  `;
}

function renderEmpty(message) {
  agentGrid.innerHTML = `<article class="panel empty-state">${escapeHtml(message)}</article>`;
}

function renderAgents(items) {
  if (!items.length) {
    renderEmpty("No agent roles registered.");
    return;
  }

  agentGrid.innerHTML = items
    .map((item) => {
      const taskTypes = item.capability_declaration.supported_task_types.length
        ? item.capability_declaration.supported_task_types
            .map((taskType) => `<span class="meta-pill">${escapeHtml(taskType)}</span>`)
            .join("")
        : '<span class="muted">No explicit task types</span>';
      const capabilities = item.capabilities.length
        ? item.capabilities.map((capability) => `<span class="meta-pill">${escapeHtml(capability)}</span>`).join("")
        : '<span class="muted">No capability tags</span>';

      return `
        <article class="panel agent-card">
          <div class="card-top">
            <div>
              <p class="section-label">Agent role</p>
              <h3>${escapeHtml(item.role_name)}</h3>
              <p class="description">${escapeHtml(item.description ?? "No description")}</p>
            </div>
            <div class="status-stack">
              <span class="status-badge ${item.enabled ? "enabled" : "disabled"}">${item.enabled ? "enabled" : "disabled"}</span>
              <span class="version-pill">v${escapeHtml(item.version)}</span>
            </div>
          </div>

          <section class="detail-block">
            <strong>Capability tags</strong>
            <div class="pill-row">${capabilities}</div>
          </section>

          <section class="detail-block">
            <strong>Supported task types</strong>
            <div class="pill-row">${taskTypes}</div>
          </section>

          <dl class="metrics compact">
            <div><dt>Total runs</dt><dd>${escapeHtml(item.total_runs)}</dd></div>
            <div><dt>Success runs</dt><dd>${escapeHtml(item.success_runs)}</dd></div>
            <div><dt>Success rate</dt><dd>${escapeHtml(formatPercent(item.success_rate))}</dd></div>
          </dl>

          <section class="detail-block">
            <strong>Input schema</strong>
            <pre class="schema-block">${formatJson(item.input_schema)}</pre>
          </section>

          <section class="detail-block">
            <strong>Output schema</strong>
            <pre class="schema-block">${formatJson(item.output_schema)}</pre>
          </section>
        </article>
      `;
    })
    .join("");
}

async function loadRegistry() {
  const params = new URLSearchParams();
  if (taskTypeInput.value.trim()) {
    params.set("task_type", taskTypeInput.value.trim());
  }

  statusText.textContent = "Loading agent registry...";

  try {
    const query = params.toString();
    const response = await fetch(`/agents/registry${query ? `?${query}` : ""}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const payload = await response.json();
    statusText.textContent = `${payload.items.length} role${payload.items.length === 1 ? "" : "s"} loaded`;
    renderSummary(payload.items);
    renderDiagnosis(payload.diagnosis);
    renderAgents(payload.items);
  } catch (error) {
    statusText.textContent = "Unable to load agent registry.";
    summaryMetrics.innerHTML = "";
    diagnosisContent.innerHTML = `<div class="empty-panel">${escapeHtml(error.message)}</div>`;
    renderEmpty(error.message);
  }
}

diagnoseButton.addEventListener("click", () => {
  loadRegistry();
});

refreshButton.addEventListener("click", () => {
  loadRegistry();
});

taskTypeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    loadRegistry();
  }
});

loadRegistry();
