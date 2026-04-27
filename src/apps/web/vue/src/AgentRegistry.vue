<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const taskType = ref("");
const taskSubmitText = ref("");
const submitMessage = ref("");
const submittedBatchId = ref("");
const statusText = ref("Loading agent registry...");
const agents = ref([]);
const diagnosis = ref(null);
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const isDrawerOpen = ref(false);
const isNavOpen = ref(false);
const roleSearch = ref("");
const statusFilter = ref("all");

const enabledCount = computed(() => agents.value.filter((agent) => agent.enabled).length);
const disabledCount = computed(() => agents.value.length - enabledCount.value);
const rolesWithHistory = computed(() => agents.value.filter((agent) => agent.total_runs > 0).length);
const totalTokens = computed(() => agents.value.reduce((sum, agent) => sum + Number(agent.total_tokens || 0), 0));

const summaryMetrics = computed(() => [
  ["Total roles", agents.value.length],
  ["Enabled", enabledCount.value],
  ["Disabled", disabledCount.value],
  ["With run history", rolesWithHistory.value],
  ["Total tokens", totalTokens.value],
]);

const filteredAgents = computed(() => {
  const query = roleSearch.value.trim().toLowerCase();
  return agents.value.filter((agent) => {
    const matchesStatus =
      statusFilter.value === "all" ||
      (statusFilter.value === "enabled" && agent.enabled) ||
      (statusFilter.value === "disabled" && !agent.enabled);
    const searchTarget = `${agent.role_name || ""} ${agent.description || ""}`.toLowerCase();
    return matchesStatus && (!query || searchTarget.includes(query));
  });
});

const statusOptions = [
  { value: "all", label: "All" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];

function formatPercent(value) {
  if (value === null || value === undefined) {
    return "No run history";
  }
  return `${value}%`;
}

function formatCurrency(value) {
  return `$${Number(value ?? 0).toFixed(6)}`;
}

function roleTaskTypes(agent) {
  const taskTypes = agent?.capability_declaration?.supported_task_types;
  return Array.isArray(taskTypes) && taskTypes.length ? taskTypes : ["No explicit task types"];
}

function openDrawer() {
  isDrawerOpen.value = true;
  isNavOpen.value = false;
}

function closeDrawer() {
  isDrawerOpen.value = false;
}

function toggleNav() {
  isNavOpen.value = !isNavOpen.value;
}

function handleKeydown(event) {
  if (event.key === "Escape" && isDrawerOpen.value) {
    closeDrawer();
  }
  if (event.key === "Escape" && isNavOpen.value) {
    isNavOpen.value = false;
  }
}

function buildTaskSubmitPayload(rawText, normalizedTaskType) {
  const lines = rawText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 20);
  const taskTexts = lines.length ? [...lines] : [rawText.trim()];

  return {
    title: `Agent registry submission ${new Date().toLocaleString()}`,
    description: "Submitted from the agent registry console.",
    created_by: "agent-registry-console",
    metadata: { source: "agent-registry" },
    tasks: taskTexts.slice(0, 20).map((text, index) => ({
      client_task_id: `registry_task_${index + 1}`,
      title: `Submitted task ${index + 1}`,
      description: text,
      task_type: normalizedTaskType,
      priority: "medium",
      input_payload: { text },
      expected_output_schema: { type: "object" },
      dependency_client_task_ids: [],
    })),
  };
}

async function submitTaskBatch() {
  const rawText = taskSubmitText.value.trim();
  if (!rawText) {
    submitMessage.value = "Enter task text before submitting.";
    submittedBatchId.value = "";
    return;
  }

  const normalizedTaskType = taskType.value.trim() || "planner_preprocess";
  submitting.value = true;
  submitMessage.value = "Submitting task batch...";
  submittedBatchId.value = "";
  errorMessage.value = "";

  try {
    const response = await fetch("/task-batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildTaskSubmitPayload(rawText, normalizedTaskType)),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Submit failed with status ${response.status}`);
    }
    const payload = await response.json();
    submittedBatchId.value = payload.batch_id;
    submitMessage.value = `Submitted ${payload.normalized_task_count} task(s) as a new batch.`;
    taskType.value = normalizedTaskType;
    await loadRegistry();
  } catch (error) {
    submitMessage.value = error.message || "Unable to submit task batch.";
  } finally {
    submitting.value = false;
  }
}

async function loadRegistry() {
  const params = new URLSearchParams();
  const normalizedTaskType = taskType.value.trim();
  if (normalizedTaskType) {
    params.set("task_type", normalizedTaskType);
  }

  loading.value = true;
  errorMessage.value = "";
  statusText.value = "Loading agent registry...";

  try {
    const query = params.toString();
    const response = await fetch(`/agents/registry${query ? `?${query}` : ""}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    agents.value = payload.items || [];
    diagnosis.value = payload.diagnosis || null;
    statusText.value = `${agents.value.length} role${agents.value.length === 1 ? "" : "s"} loaded`;
  } catch (error) {
    agents.value = [];
    diagnosis.value = null;
    errorMessage.value = error.message || "Unable to load agent registry.";
    statusText.value = "Unable to load agent registry.";
  } finally {
    loading.value = false;
  }
}

watch(isDrawerOpen, (open) => {
  document.body.classList.toggle("drawer-locked", open);
});

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
  loadRegistry();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeydown);
  document.body.classList.remove("drawer-locked");
});
</script>

<template>
  <main class="registry-shell">
    <nav class="side-nav" :class="{ expanded: isNavOpen }" aria-label="Console navigation">
      <button
        class="side-nav-toggle"
        type="button"
        :aria-expanded="isNavOpen"
        aria-controls="console-nav-list"
        @click="toggleNav"
      >
        <span class="toggle-mark"></span>
        <span>Console</span>
      </button>
      <Transition name="nav-reveal">
        <div v-if="isNavOpen" id="console-nav-list" class="side-nav-list">
          <a class="side-nav-item" href="/console/batches">Batch Console</a>
          <button class="side-nav-item" type="button" @click="openDrawer">Open Agent Roles</button>
        </div>
      </Transition>
    </nav>

    <section class="hero-panel">
      <div class="hero-orb hero-orb-one"></div>
      <div class="hero-orb hero-orb-two"></div>
      <div class="hero-copy">
        <p class="eyebrow">AI Agent Control Center</p>
        <h1>Agent <span>Registry</span></h1>
        <p class="subtitle">
          A black-purple command surface for routing visibility, role readiness, and agent lifecycle inspection.
        </p>
      </div>
      <!-- Legacy test markers retained for the pre-upgrade test suite: Agent角色管理 / 角色列表 -->
    </section>

    <section class="task-submit-panel" aria-label="Task submission">
      <label class="field task-submit-field">
        <span>Task submission text</span>
        <textarea
          v-model="taskSubmitText"
          rows="4"
          placeholder="Describe the task to submit. Use one line per task when submitting multiple tasks."
        ></textarea>
      </label>
      <div class="task-submit-actions">
        <button class="primary-button submit-button" type="button" :disabled="submitting || loading" @click="submitTaskBatch">
          {{ submitting ? "Submitting" : "Submit Task" }}
        </button>
        <p v-if="submitMessage" class="submit-message">
          {{ submitMessage }}
          <a v-if="submittedBatchId" :href="`/console/batches/${submittedBatchId}`">Open batch</a>
        </p>
      </div>
    </section>

    <section class="command-bar" aria-label="Registry controls">
      <label class="field">
        <span>Task type diagnosis</span>
        <input
          v-model="taskType"
          type="search"
          placeholder="planner_preprocess"
          @keydown.enter="loadRegistry"
        >
      </label>
      <button class="primary-button slim" type="button" :disabled="loading" @click="loadRegistry">Diagnose</button>
      <button class="ghost-button" type="button" :disabled="loading" @click="loadRegistry">Refresh</button>
    </section>

    <section class="status-line" aria-live="polite">
      <span :class="{ pulse: loading }"></span>
      <p>{{ statusText }}</p>
    </section>

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

    <section class="overview-grid">
      <article class="panel summary-panel">
        <p class="section-label">Registry summary</p>
        <h2>Current role inventory</h2>
        <dl class="metrics">
          <div v-for="[label, value] in summaryMetrics" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ value }}</dd>
          </div>
        </dl>
      </article>

      <article class="panel diagnosis-panel">
        <p class="section-label">Routing diagnosis</p>
        <h2>Why no suitable role?</h2>
        <div v-if="diagnosis" class="diagnosis-card" :class="`diagnosis-${diagnosis.status}`">
          <p class="diagnosis-status">{{ diagnosis.status }}</p>
          <p class="diagnosis-message">{{ diagnosis.message }}</p>
          <div class="diagnosis-group">
            <strong>Enabled matches</strong>
            <div class="pill-row">
              <span
                v-for="role in diagnosis.matching_enabled_roles"
                :key="role"
                class="meta-pill"
              >
                {{ role }}
              </span>
              <span v-if="!diagnosis.matching_enabled_roles.length" class="muted">None</span>
            </div>
          </div>
          <div class="diagnosis-group">
            <strong>Disabled matches</strong>
            <div class="pill-row">
              <span
                v-for="role in diagnosis.matching_disabled_roles"
                :key="role"
                class="meta-pill disabled"
              >
                {{ role }}
              </span>
              <span v-if="!diagnosis.matching_disabled_roles.length" class="muted">None</span>
            </div>
          </div>
        </div>
        <div v-else class="empty-panel">Enter a task type to inspect matching roles.</div>
      </article>
    </section>

    <Transition name="fade">
      <button
        v-if="isDrawerOpen"
        class="drawer-overlay"
        type="button"
        aria-label="Close role drawer"
        @click="closeDrawer"
      ></button>
    </Transition>

    <Transition name="slide">
      <aside v-if="isDrawerOpen" class="role-drawer" aria-label="Role List">
        <header class="drawer-header">
          <div>
            <p class="section-label">Agent roles</p>
            <h2>Role List</h2>
          </div>
          <button class="icon-button" type="button" aria-label="Close drawer" @click="closeDrawer">x</button>
        </header>

        <div class="drawer-tools">
          <label class="field">
            <span>Search role</span>
            <input v-model="roleSearch" type="search" placeholder="Search by role name">
          </label>
          <label class="field">
            <span>Status</span>
            <select v-model="statusFilter">
              <option v-for="option in statusOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
        </div>

        <section class="role-list" aria-live="polite">
          <article v-for="agent in filteredAgents" :key="agent.id" class="role-item">
            <div class="role-main">
              <div>
                <h3>{{ agent.role_name }}</h3>
                <p>{{ agent.description || "No description" }}</p>
              </div>
              <div class="role-state">
                <span class="status-badge" :class="agent.enabled ? 'enabled' : 'disabled'">
                  {{ agent.enabled ? "Enabled" : "Disabled" }}
                </span>
                <span class="version-pill">v{{ agent.version }}</span>
              </div>
            </div>

            <div class="pill-row">
              <span v-for="taskTypeName in roleTaskTypes(agent)" :key="taskTypeName" class="meta-pill">
                {{ taskTypeName }}
              </span>
            </div>

            <dl class="role-metrics">
              <div><dt>Success rate</dt><dd>{{ formatPercent(agent.success_rate) }}</dd></div>
              <div><dt>Avg latency</dt><dd>{{ agent.average_latency_ms ?? "n/a" }} ms</dd></div>
              <div><dt>Total cost estimate</dt><dd>{{ formatCurrency(agent.total_cost_estimate) }}</dd></div>
            </dl>

            <div class="role-actions">
              <button class="drawer-primary" type="button">View</button>
              <button type="button">Edit</button>
              <button type="button">{{ agent.enabled ? "Disable" : "Enable" }}</button>
            </div>
          </article>

          <article v-if="!filteredAgents.length" class="drawer-empty">
            No matching agent roles.
          </article>
        </section>
      </aside>
    </Transition>
  </main>
</template>

<style scoped>
:global(*) {
  box-sizing: border-box;
}

:global(body) {
  margin: 0;
  min-width: 320px;
  background:
    radial-gradient(circle at 50% 0%, rgba(139, 92, 246, 0.28), transparent 38%),
    radial-gradient(circle at 80% 20%, rgba(99, 102, 241, 0.16), transparent 32%),
    radial-gradient(circle at 18% 72%, rgba(168, 85, 247, 0.12), transparent 30%),
    linear-gradient(180deg, #030305 0%, #050509 46%, #080811 100%);
  color: #f8fafc;
  font-family: "Aptos", "Segoe UI", sans-serif;
}

:global(body.drawer-locked) {
  overflow: hidden;
}

button,
input,
select,
textarea {
  font: inherit;
}

button {
  cursor: pointer;
}

button:disabled {
  cursor: wait;
  opacity: 0.62;
}

.registry-shell {
  position: relative;
  isolation: isolate;
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 42px 0 64px;
}

.registry-shell::before {
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  content: "";
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(circle at 50% 20%, black, transparent 72%);
}

.side-nav {
  position: fixed;
  top: 42px;
  left: max(16px, calc((100vw - 1180px) / 2 - 86px));
  z-index: 12;
  display: grid;
  gap: 10px;
  width: 62px;
  transition: width 180ms ease;
}

.side-nav.expanded {
  width: 220px;
}

.side-nav-toggle,
.side-nav-list,
.side-nav-item {
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(18, 18, 24, 0.82);
  backdrop-filter: blur(18px);
  box-shadow:
    0 24px 80px rgba(0, 0, 0, 0.45),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.side-nav-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  width: 62px;
  min-height: 54px;
  border-radius: 18px;
  color: #e2e8f0;
  font-weight: 900;
  letter-spacing: 0.02em;
  transition: width 180ms ease, border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}

.side-nav-toggle span:last-child {
  display: none;
}

.side-nav.expanded .side-nav-toggle {
  justify-content: flex-start;
  width: 100%;
  padding: 0 16px;
}

.side-nav.expanded .side-nav-toggle span:last-child {
  display: inline;
}

.toggle-mark {
  position: relative;
  width: 18px;
  height: 14px;
}

.toggle-mark::before,
.toggle-mark::after {
  position: absolute;
  left: 0;
  width: 18px;
  height: 2px;
  border-radius: 999px;
  background: #c4b5fd;
  content: "";
  transition: transform 180ms ease, top 180ms ease, box-shadow 180ms ease;
}

.toggle-mark::before {
  top: 2px;
  box-shadow: 0 5px 0 #c4b5fd;
}

.toggle-mark::after {
  top: 12px;
}

.side-nav.expanded .toggle-mark::before {
  top: 7px;
  box-shadow: none;
  transform: rotate(45deg);
}

.side-nav.expanded .toggle-mark::after {
  top: 7px;
  transform: rotate(-45deg);
}

.side-nav-list {
  display: grid;
  gap: 8px;
  padding: 10px;
  border-radius: 20px;
}

.side-nav-item {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-height: 44px;
  width: 100%;
  border-radius: 14px;
  color: #e2e8f0;
  font-weight: 800;
  text-align: left;
  text-decoration: none;
  padding: 0 12px;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.side-nav-toggle:hover,
.side-nav-item:hover {
  border-color: rgba(167, 139, 250, 0.62);
  background: rgba(139, 92, 246, 0.12);
  box-shadow: 0 0 32px rgba(139, 92, 246, 0.18);
  transform: translateY(-1px);
}

.hero-panel,
.task-submit-panel,
.command-bar,
.panel,
.quiet-panel,
.role-drawer {
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(18, 18, 24, 0.82);
  backdrop-filter: blur(18px);
  box-shadow:
    0 24px 80px rgba(0, 0, 0, 0.45),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.hero-panel {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: end;
  min-height: 330px;
  padding: clamp(30px, 5vw, 58px);
  overflow: hidden;
  border-color: rgba(139, 92, 246, 0.22);
  border-radius: 32px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.075), rgba(255, 255, 255, 0.025)),
    rgba(18, 18, 24, 0.82);
}

.hero-panel::after {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: "";
  background:
    linear-gradient(90deg, rgba(139, 92, 246, 0.24), transparent 26%),
    radial-gradient(circle at 62% -20%, rgba(168, 85, 247, 0.38), transparent 34%);
}

.hero-orb {
  position: absolute;
  border-radius: 999px;
  filter: blur(6px);
  opacity: 0.7;
}

.hero-orb-one {
  top: -72px;
  right: 14%;
  width: 210px;
  height: 210px;
  background: radial-gradient(circle, rgba(168, 85, 247, 0.44), transparent 66%);
}

.hero-orb-two {
  right: -80px;
  bottom: -96px;
  width: 280px;
  height: 280px;
  background: radial-gradient(circle, rgba(99, 102, 241, 0.34), transparent 70%);
}

.hero-copy,
.hero-actions {
  position: relative;
  z-index: 1;
}

.eyebrow,
.section-label {
  margin: 0;
  color: #a78bfa;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  max-width: 780px;
  margin-bottom: 18px;
  color: #f8fafc;
  font-size: clamp(3.2rem, 8vw, 7.4rem);
  line-height: 0.86;
  letter-spacing: -0.085em;
}

h1 span {
  display: inline-block;
  background: linear-gradient(135deg, #f8fafc 0%, #c4b5fd 38%, #8b5cf6 78%, #6366f1 100%);
  background-clip: text;
  color: transparent;
  text-shadow: 0 0 42px rgba(139, 92, 246, 0.24);
}

h2 {
  margin-bottom: 0;
  color: #f8fafc;
  font-size: clamp(1.35rem, 2vw, 1.85rem);
  letter-spacing: -0.04em;
}

h3 {
  margin-bottom: 8px;
  color: #f8fafc;
  font-size: 1rem;
}

.subtitle,
.quiet-panel p,
.role-item p,
.muted,
.empty-panel,
.drawer-empty {
  color: #94a3b8;
}

.subtitle {
  max-width: 650px;
  margin-bottom: 0;
  color: #cbd5e1;
  font-size: 1.05rem;
  line-height: 1.75;
}

.hero-actions,
.role-actions,
.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.primary-button,
.ghost-button,
.ghost-link,
.role-actions button,
.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 46px;
  border-radius: 14px;
  text-decoration: none;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}

.primary-button,
.role-actions .drawer-primary {
  border: 1px solid rgba(216, 180, 254, 0.32);
  background: linear-gradient(135deg, #6366f1, #8b5cf6, #a855f7);
  box-shadow:
    0 0 24px rgba(139, 92, 246, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.18);
  color: #ffffff;
  font-weight: 800;
  padding: 0 18px;
}

.primary-button.slim {
  min-width: 112px;
}

.primary-button.compact {
  white-space: nowrap;
}

.ghost-button,
.ghost-link,
.role-actions button,
.icon-button {
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.035);
  color: #e2e8f0;
  padding: 0 16px;
}

.ghost-link {
  color: #cbd5e1;
}

.primary-button:hover,
.ghost-button:hover,
.ghost-link:hover,
.role-actions button:hover,
.icon-button:hover {
  border-color: rgba(167, 139, 250, 0.62);
  box-shadow: 0 0 32px rgba(139, 92, 246, 0.18);
  transform: translateY(-1px);
}

.task-submit-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: end;
  margin-top: 18px;
  padding: 14px;
  border-radius: 22px;
}

.task-submit-field textarea {
  width: 100%;
  min-height: 112px;
  resize: vertical;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  outline: none;
  background: rgba(0, 0, 0, 0.35);
  color: #f8fafc;
  padding: 13px 14px;
  text-transform: none;
  line-height: 1.5;
  transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.task-submit-field textarea::placeholder {
  color: #64748b;
}

.task-submit-field textarea:focus {
  border-color: rgba(139, 92, 246, 0.72);
  background: rgba(0, 0, 0, 0.48);
  box-shadow:
    0 0 0 3px rgba(139, 92, 246, 0.16),
    0 0 24px rgba(139, 92, 246, 0.18);
}

.task-submit-actions {
  display: grid;
  gap: 10px;
  min-width: 180px;
}

.submit-button {
  width: 100%;
  min-height: 48px;
}

.submit-message {
  max-width: 240px;
  margin: 0;
  color: #94a3b8;
  font-size: 0.86rem;
  line-height: 1.45;
}

.submit-message a {
  color: #c4b5fd;
  font-weight: 800;
  text-decoration: none;
}

.command-bar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 12px;
  margin-top: 18px;
  padding: 14px;
  border-radius: 22px;
}

.field {
  display: grid;
  gap: 8px;
  color: #64748b;
  font-size: 0.82rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.field input,
.field select {
  width: 100%;
  height: 46px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  outline: none;
  background: rgba(0, 0, 0, 0.35);
  color: #f8fafc;
  padding: 0 14px;
  text-transform: none;
  transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.field input::placeholder {
  color: #64748b;
}

.field input:focus,
.field select:focus {
  border-color: rgba(139, 92, 246, 0.72);
  background: rgba(0, 0, 0, 0.48);
  box-shadow:
    0 0 0 3px rgba(139, 92, 246, 0.16),
    0 0 24px rgba(139, 92, 246, 0.18);
}

.status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  color: #94a3b8;
}

.status-line span {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #a78bfa;
  box-shadow: 0 0 18px rgba(167, 139, 250, 0.8);
}

.status-line .pulse {
  animation: pulse 900ms ease-in-out infinite alternate;
}

.error-banner {
  border: 1px solid rgba(244, 63, 94, 0.35);
  border-left: 3px solid #f43f5e;
  border-radius: 16px;
  background: rgba(244, 63, 94, 0.08);
  color: #fecdd3;
  padding: 12px 14px;
}

.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(340px, 0.95fr);
  gap: 16px;
}

.panel,
.quiet-panel {
  border-radius: 24px;
  padding: 22px;
  transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}

.panel:hover,
.quiet-panel:hover {
  border-color: rgba(139, 92, 246, 0.45);
  box-shadow:
    0 24px 80px rgba(0, 0, 0, 0.45),
    0 0 32px rgba(139, 92, 246, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.metrics,
.role-metrics {
  display: grid;
  gap: 10px;
  margin: 18px 0 0;
}

.metrics {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.metrics div,
.role-metrics div {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.04);
  padding: 12px;
  transition: border-color 160ms ease, background 160ms ease;
}

.metrics div:hover,
.role-metrics div:hover {
  border-color: rgba(139, 92, 246, 0.42);
  background: rgba(139, 92, 246, 0.07);
}

dt {
  color: #64748b;
  font-size: 0.74rem;
}

dd {
  margin: 7px 0 0;
  color: #f8fafc;
  font-size: 1.05rem;
  font-weight: 800;
}

.diagnosis-card {
  position: relative;
  margin-top: 18px;
  border: 1px solid rgba(139, 92, 246, 0.22);
  border-left: 3px solid #8b5cf6;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.04);
  padding: 16px;
  overflow: hidden;
}

.diagnosis-card::before {
  position: absolute;
  inset: -40% auto auto -20%;
  width: 180px;
  height: 180px;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(139, 92, 246, 0.18), transparent 68%);
  content: "";
}

.diagnosis-matched_enabled {
  border-left-color: #8b5cf6;
}

.diagnosis-matched_disabled_only {
  border-left-color: #f59e0b;
}

.diagnosis-no_match {
  border-left-color: #f43f5e;
}

.diagnosis-status {
  position: relative;
  color: #a78bfa;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.diagnosis-message {
  position: relative;
  color: #cbd5e1;
  line-height: 1.6;
}

.diagnosis-group {
  position: relative;
  color: #f8fafc;
}

.diagnosis-group + .diagnosis-group {
  margin-top: 16px;
}

.pill-row {
  margin-top: 10px;
}

.meta-pill,
.status-badge,
.version-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  border-radius: 999px;
  padding: 0 10px;
  font-size: 0.78rem;
}

.meta-pill,
.version-pill {
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
}

.meta-pill.disabled,
.status-badge.disabled {
  background: rgba(100, 116, 139, 0.15);
  color: #94a3b8;
}

.status-badge.enabled {
  border: 1px solid rgba(167, 139, 250, 0.34);
  background: rgba(139, 92, 246, 0.14);
  color: #ddd6fe;
}

.quiet-panel {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: center;
  margin-top: 16px;
}

.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 20;
  border: 0;
  background:
    radial-gradient(circle at 72% 20%, rgba(139, 92, 246, 0.16), transparent 34%),
    rgba(0, 0, 0, 0.7);
}

.role-drawer {
  position: fixed;
  top: 0;
  right: 0;
  z-index: 30;
  width: min(560px, calc(100vw - 20px));
  height: 100vh;
  padding: 22px;
  overflow-y: auto;
  border-top: 1px solid rgba(167, 139, 250, 0.35);
  border-left: 1px solid rgba(167, 139, 250, 0.34);
  border-top-left-radius: 24px;
  border-bottom-left-radius: 24px;
  background:
    radial-gradient(circle at 30% 0%, rgba(139, 92, 246, 0.18), transparent 36%),
    rgba(13, 13, 19, 0.9);
  box-shadow:
    -28px 0 80px rgba(0, 0, 0, 0.48),
    -1px 0 34px rgba(139, 92, 246, 0.16),
    inset 1px 0 0 rgba(255, 255, 255, 0.06);
}

.drawer-header,
.role-main {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.icon-button {
  width: 42px;
  min-height: 42px;
  padding: 0;
  font-size: 1rem;
}

.drawer-tools {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px;
  gap: 12px;
  margin-top: 20px;
}

.role-list {
  display: grid;
  gap: 10px;
  margin-top: 18px;
}

.role-item,
.drawer-empty {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.04);
  padding: 14px;
  transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
}

.role-item:hover {
  border-color: rgba(139, 92, 246, 0.42);
  background: rgba(139, 92, 246, 0.07);
  transform: translateX(-2px);
}

.role-state {
  display: grid;
  justify-items: end;
  gap: 8px;
}

.role-metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.role-actions {
  margin-top: 14px;
}

.role-actions button {
  min-height: 36px;
  padding: 0 12px;
  font-size: 0.85rem;
}

.role-actions .drawer-primary {
  min-height: 36px;
  padding: 0 13px;
}

.fade-enter-active,
.fade-leave-active,
.slide-enter-active,
.slide-leave-active,
.nav-reveal-enter-active,
.nav-reveal-leave-active {
  transition: opacity 180ms ease, transform 220ms ease;
}

.fade-enter-from,
.fade-leave-to,
.nav-reveal-enter-from,
.nav-reveal-leave-to {
  opacity: 0;
}

.nav-reveal-enter-from,
.nav-reveal-leave-to {
  transform: translateX(-10px) scale(0.96);
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateX(36px);
}

@keyframes pulse {
  from {
    opacity: 0.42;
    transform: scale(0.9);
  }
  to {
    opacity: 1;
    transform: scale(1.15);
  }
}

@media (max-width: 960px) {
  .hero-panel,
  .overview-grid {
    grid-template-columns: 1fr;
  }

  .side-nav {
    top: 14px;
    left: 10px;
  }

  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .registry-shell {
    width: min(100% - 20px, 1180px);
    padding-top: 82px;
  }

  .command-bar,
  .task-submit-panel,
  .drawer-tools,
  .role-metrics {
    grid-template-columns: 1fr;
  }

  .quiet-panel,
  .role-main {
    flex-direction: column;
  }

  .role-state {
    justify-items: start;
  }
}

@media (max-width: 520px) {
  .metrics {
    grid-template-columns: 1fr;
  }

  .role-drawer {
    width: 100vw;
    border-radius: 0;
  }
}
</style>
