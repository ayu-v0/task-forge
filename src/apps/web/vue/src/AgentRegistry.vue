<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const taskSubmitText = ref("");
const submitMessage = ref("");
const submittedBatchId = ref("");
const statusText = ref("Loading agent registry...");
const agents = ref([]);
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const isDrawerOpen = ref(false);
const isSideMenuOpen = ref(false);
const isRoleDetailOpen = ref(false);
const selectedAgentId = ref("");
const selectedAgentDetail = ref(null);
const detailLoading = ref(false);
const detailError = ref("");
const isRoleEditOpen = ref(false);
const editingAgentId = ref("");
const editForm = ref(createEmptyEditForm());
const editLoading = ref(false);
const editSaving = ref(false);
const editError = ref("");
const editMessage = ref("");
const roleSearch = ref("");
const statusFilter = ref("all");
const isBatchWindowOpen = ref(false);
const batchSearch = ref("");
const batchStatusFilter = ref("");
const batchSort = ref("created_at_desc");
const batchStatusText = ref("Loading batches...");
const batchLoading = ref(false);
const batchError = ref("");
const batches = ref([]);
const selectedBatchId = ref("");
const selectedBatchSummary = ref(null);
const selectedTaskId = ref("");
const selectedTaskTimelineItems = ref([]);

let batchAbortController = null;
let batchDetailAbortController = null;
let taskTimelineAbortController = null;

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

const selectedBatchTask = computed(() => {
  return selectedBatchSummary.value?.tasks?.find((task) => task.task_id === selectedTaskId.value) || null;
});

const selectedTaskArtifacts = computed(() => {
  const allArtifacts = selectedBatchSummary.value?.artifacts || [];
  const artifacts = selectedTaskId.value
    ? allArtifacts.filter((artifact) => artifact.task_id === selectedTaskId.value)
    : allArtifacts;
  const hasStoredFileLevelDeliverable = artifacts.some((artifact) => ["code_file", "code_patch"].includes(artifact.artifact_type));
  const displayArtifacts = hasStoredFileLevelDeliverable
    ? artifacts
    : [...inferCodeArtifactsFromJsonArtifacts(artifacts), ...artifacts];
  return [...displayArtifacts].sort((left, right) => artifactPriority(left) - artifactPriority(right));
});

const selectedTaskMissingFileDeliverable = computed(() => {
  const task = selectedBatchTask.value;
  if (!task || !selectedTaskLooksLikeCode(task)) {
    return false;
  }
  return !selectedTaskArtifacts.value.some((artifact) => ["code_file", "code_patch"].includes(artifact.artifact_type));
});

const statusOptions = [
  { value: "all", label: "All" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];

const batchStatusOptions = [
  { value: "", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "needs_review", label: "Needs review" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "partially_failed", label: "Partially failed" },
];

const batchSortOptions = [
  { value: "created_at_desc", label: "Newest created" },
  { value: "created_at_asc", label: "Oldest created" },
  { value: "updated_at_desc", label: "Recently updated" },
  { value: "updated_at_asc", label: "Least recently updated" },
];

const promptBudgetHighlights = [
  ["Template", "template_name"],
  ["Context limit", "model_context_limit"],
  ["Reserved output", "reserved_output_tokens"],
  ["Task input", "max_task_input_tokens"],
];

function createEmptyEditForm() {
  return {
    roleName: "",
    enabled: true,
    version: "",
    timeoutSeconds: 300,
    maxRetries: 0,
  };
}

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

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value || "n/a";
  }
  return date.toLocaleString();
}

function hasObjectContent(value) {
  return value && typeof value === "object" && Object.keys(value).length > 0;
}

function previewText(value, limit = 1200) {
  const text = String(value ?? "");
  return text.length <= limit ? text : `${text.slice(0, limit)}...`;
}

function lineCount(value) {
  const text = String(value ?? "");
  return text ? text.split(/\r?\n/).length : 0;
}

function generatedPathForLanguage(taskId, language) {
  const extensions = {
    python: ".py",
    py: ".py",
    javascript: ".js",
    js: ".js",
    typescript: ".ts",
    ts: ".ts",
    go: ".go",
    golang: ".go",
    powershell: ".ps1",
    ps1: ".ps1",
    shell: ".sh",
    sh: ".sh",
  };
  const normalizedLanguage = String(language || "text").trim().toLowerCase();
  return `generated/${taskId || "task"}${extensions[normalizedLanguage] || ".txt"}`;
}

function contentTypeForLanguage(language) {
  const contentTypes = {
    python: "text/x-python",
    py: "text/x-python",
    javascript: "text/javascript",
    js: "text/javascript",
    typescript: "text/typescript",
    ts: "text/typescript",
    go: "text/x-go",
    golang: "text/x-go",
    powershell: "text/x-powershell",
    ps1: "text/x-powershell",
    shell: "text/x-shellscript",
    sh: "text/x-shellscript",
  };
  return contentTypes[String(language || "").trim().toLowerCase()] || "text/plain";
}

function findLegacyCodeResult(value, depth = 0) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (typeof value.code === "string" && value.code.trim()) {
    return value;
  }
  if (depth >= 3) {
    return null;
  }
  for (const key of ["result", "output", "artifact"]) {
    const found = findLegacyCodeResult(value[key], depth + 1);
    if (found) {
      return found;
    }
  }
  return null;
}

function inferCodeArtifactsFromJsonArtifacts(artifacts) {
  return artifacts
    .filter((artifact) => artifact.artifact_type === "json")
    .map((artifact) => {
      const codeResult = findLegacyCodeResult(artifact.raw_content || artifact.structured_output || {});
      if (!codeResult) {
        return null;
      }
      const language = String(codeResult.language || "text").trim().toLowerCase() || "text";
      const path = String(
        codeResult.path ||
        codeResult.file_path ||
        codeResult.filename ||
        generatedPathForLanguage(artifact.task_id, language),
      ).replaceAll("\\", "/");
      const content = codeResult.code;
      return {
        ...artifact,
        artifact_id: `${artifact.artifact_id || artifact.run_id || "artifact"}:inferred-code-file`,
        artifact_type: "code_file",
        deliverable_type: "code",
        uri: `workspace://${path}`,
        content_type: contentTypeForLanguage(language),
        raw_content: { path, content },
        summary: { path, language, change_type: "generated" },
        structured_output: {
          path,
          language,
          change_type: "generated",
          line_count: lineCount(content),
          content_preview: previewText(content),
          inferred_from: artifact.artifact_id,
        },
        metadata: {
          ...(artifact.metadata || {}),
          artifact_role: "final_deliverable",
          deliverable_type: "code",
          inferred_from_artifact_type: "json",
        },
      };
    })
    .filter(Boolean);
}

function selectedTaskLooksLikeCode(task) {
  if (!task) {
    return false;
  }
  const haystack = `${task.task_type || ""} ${task.title || ""} ${task.description || ""}`.toLowerCase();
  return [
    "code",
    "implement",
    "fix",
    "bug",
    "test",
    "refactor",
    "script",
    "config",
    "代码",
    "实现",
    "修复",
    "测试",
    "脚本",
    "配置",
  ].some((keyword) => haystack.includes(keyword));
}

function artifactPriority(artifact) {
  const priorities = {
    code_file: 0,
    code_patch: 1,
    test_report: 2,
    document: 3,
    analysis_report: 4,
    data_file: 5,
    generic_result: 8,
    json: 9,
  };
  return priorities[artifact.artifact_type] ?? 6;
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
  return labels[stage] || stage || "Status";
}

function artifactTitle(artifact) {
  const summary = artifact.summary || {};
  const rawContent = artifact.raw_content || {};
  if (artifact.artifact_type === "code_file") {
    return summary.path || rawContent.path || "Code file";
  }
  if (artifact.artifact_type === "code_patch") {
    return "Code patch";
  }
  if (artifact.artifact_type === "test_report") {
    return "Test report";
  }
  return summary.title || rawContent.title || artifact.artifact_type || "Artifact";
}

function artifactDeliverableType(artifact) {
  const metadata = artifact.metadata || {};
  const structuredOutput = artifact.structured_output || {};
  const summary = artifact.summary || {};
  const value =
    artifact.deliverable_type ||
    metadata.deliverable_type ||
    structuredOutput.deliverable_type ||
    summary.deliverable_type ||
    "";
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized) {
    return normalized;
  }
  if (["code_file", "code_patch"].includes(artifact.artifact_type)) {
    return "code";
  }
  if (artifact.content_type === "text/markdown") {
    return "markdown";
  }
  if (artifact.content_type === "text/plain") {
    return "txt";
  }
  if (artifact.artifact_type === "json") {
    return "json";
  }
  return artifact.artifact_type || "artifact";
}

function artifactDeliverableLabel(artifact) {
  const labels = {
    markdown: "Markdown",
    txt: "TXT",
    code: "Code",
    json: "JSON",
  };
  const deliverableType = artifactDeliverableType(artifact);
  return labels[deliverableType] || deliverableType;
}

function artifactPreview(artifact) {
  const summary = artifact.summary || {};
  const structuredOutput = artifact.structured_output || {};
  const rawContent = artifact.raw_content || {};
  if (artifact.artifact_type === "code_file") {
    return structuredOutput.content_preview || previewText(rawContent.content || "");
  }
  if (artifact.artifact_type === "code_patch") {
    return structuredOutput.diff_preview || previewText(rawContent.diff || "");
  }
  if (artifact.artifact_type === "test_report") {
    return structuredOutput.output_preview || previewText(rawContent.output || "");
  }
  return structuredOutput.content_preview || previewText(rawContent.content || JSON.stringify(summary || {}, null, 2));
}

function fullArtifactContent(artifact) {
  const rawContent = artifact.raw_content || {};
  if (artifact.artifact_type === "code_file") {
    return rawContent.content || "";
  }
  if (artifact.artifact_type === "code_patch") {
    return rawContent.diff || "";
  }
  if (artifact.artifact_type === "test_report") {
    return rawContent.output || "";
  }
  return hasObjectContent(rawContent) ? JSON.stringify(rawContent, null, 2) : "";
}

function artifactDownloadContent(artifact) {
  const rawContent = artifact.raw_content || {};
  if (artifact.artifact_type === "code_file") {
    return rawContent.content || "";
  }
  if (artifact.artifact_type === "code_patch") {
    return rawContent.diff || "";
  }
  if (artifact.artifact_type === "test_report") {
    return rawContent.output || "";
  }
  if (["document", "analysis_report", "data_file"].includes(artifact.artifact_type)) {
    return rawContent.content || rawContent.body || "";
  }
  const fullContent = fullArtifactContent(artifact);
  if (fullContent) {
    return fullContent;
  }
  if (hasObjectContent(rawContent)) {
    return JSON.stringify(rawContent, null, 2);
  }
  return artifactPreview(artifact) || "";
}

function artifactDownloadFilename(artifact) {
  const rawContent = artifact.raw_content || {};
  const path = String(rawContent.path || "").replaceAll("\\", "/");
  const pathName = path.split("/").filter(Boolean).pop();
  if (pathName) {
    return pathName;
  }
  const extensions = {
    markdown: ".md",
    txt: ".txt",
    code: ".txt",
    json: ".json",
  };
  const deliverableType = artifactDeliverableType(artifact);
  const extension = artifact.artifact_type === "code_patch" ? ".diff" : extensions[deliverableType] || ".txt";
  return `${artifact.task_id || artifact.artifact_id || "artifact"}-${artifact.artifact_type || "deliverable"}${extension}`;
}

function downloadArtifact(artifact) {
  const content = artifactDownloadContent(artifact);
  const blob = new Blob([content], { type: artifact.content_type || "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = artifactDownloadFilename(artifact);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function openDrawer() {
  isDrawerOpen.value = true;
}

function closeDrawer() {
  isDrawerOpen.value = false;
  closeRoleDetail();
  closeRoleEdit();
}

function toggleSideMenu() {
  isSideMenuOpen.value = !isSideMenuOpen.value;
}

function closeSideMenu() {
  isSideMenuOpen.value = false;
}

function openRolesFromSideMenu() {
  openDrawer();
  closeSideMenu();
}

function closeRoleDetail() {
  isRoleDetailOpen.value = false;
  selectedAgentId.value = "";
  selectedAgentDetail.value = null;
  detailLoading.value = false;
  detailError.value = "";
}

function closeRoleEdit() {
  isRoleEditOpen.value = false;
  editingAgentId.value = "";
  editForm.value = createEmptyEditForm();
  editLoading.value = false;
  editSaving.value = false;
  editError.value = "";
  editMessage.value = "";
}

function abortBatchRequests() {
  if (batchAbortController) {
    batchAbortController.abort();
    batchAbortController = null;
  }
  if (batchDetailAbortController) {
    batchDetailAbortController.abort();
    batchDetailAbortController = null;
  }
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
    taskTimelineAbortController = null;
  }
}

async function openBatchWindow(batchId = "") {
  closeSideMenu();
  closeDrawer();
  closeRoleDetail();
  closeRoleEdit();
  isBatchWindowOpen.value = true;
  batchError.value = "";
  document.body.classList.add("modal-locked");
  await loadBatches();
  if (batchId) {
    await openBatchDetail(batchId);
  }
}

function closeBatchWindow() {
  abortBatchRequests();
  isBatchWindowOpen.value = false;
  selectedBatchId.value = "";
  selectedBatchSummary.value = null;
  selectedTaskId.value = "";
  selectedTaskTimelineItems.value = [];
  document.body.classList.remove("modal-locked");
}

async function loadBatches() {
  if (batchAbortController) {
    batchAbortController.abort();
  }
  batchAbortController = new AbortController();

  const params = new URLSearchParams();
  if (batchSearch.value.trim()) {
    params.set("search", batchSearch.value.trim());
  }
  if (batchStatusFilter.value) {
    params.set("status", batchStatusFilter.value);
  }
  params.set("sort", batchSort.value);

  batchLoading.value = true;
  batchError.value = "";
  batchStatusText.value = "Loading batches...";

  try {
    const response = await fetch(`/task-batches?${params.toString()}`, {
      signal: batchAbortController.signal,
    });
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    batches.value = payload.items || [];
    batchStatusText.value = `${batches.value.length} batch${batches.value.length === 1 ? "" : "es"} shown`;
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    batches.value = [];
    batchError.value = error.message || "Unable to load batches.";
    batchStatusText.value = "Unable to load batches.";
  } finally {
    batchLoading.value = false;
  }
}

async function openBatchDetail(batchId) {
  if (batchDetailAbortController) {
    batchDetailAbortController.abort();
  }
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
    taskTimelineAbortController = null;
  }
  batchDetailAbortController = new AbortController();

  selectedBatchId.value = batchId;
  selectedBatchSummary.value = null;
  selectedTaskId.value = "";
  selectedTaskTimelineItems.value = [];
  batchError.value = "";

  try {
    const response = await fetch(`/task-batches/${encodeURIComponent(batchId)}/summary`, {
      signal: batchDetailAbortController.signal,
    });
    if (response.status === 404) {
      throw new Error("Batch not found.");
    }
    if (!response.ok) {
      throw new Error(`Summary request failed with status ${response.status}`);
    }
    const summary = await response.json();
    if (selectedBatchId.value !== batchId) {
      return;
    }
    selectedBatchSummary.value = summary;
    const firstTask = summary.tasks?.[0];
    if (firstTask) {
      selectedTaskId.value = firstTask.task_id;
      await loadSelectedTaskTimeline(firstTask);
    }
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    batchError.value = error.message || "Unable to load batch detail.";
  }
}

function closeBatchTaskDetail() {
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
    taskTimelineAbortController = null;
  }
  selectedTaskId.value = "";
  selectedTaskTimelineItems.value = [];
}

async function loadSelectedTaskTimeline(task) {
  if (taskTimelineAbortController) {
    taskTimelineAbortController.abort();
  }
  taskTimelineAbortController = new AbortController();

  try {
    const response = await fetch(`/tasks/${encodeURIComponent(task.task_id)}/timeline`, {
      signal: taskTimelineAbortController.signal,
    });
    if (response.status === 404) {
      throw new Error("Task timeline not found.");
    }
    if (!response.ok) {
      throw new Error(`Task timeline request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (selectedTaskId.value === task.task_id) {
      selectedTaskTimelineItems.value = payload.items || [];
    }
  } catch (error) {
    if (error.name === "AbortError" || selectedTaskId.value !== task.task_id) {
      return;
    }
    batchError.value = error.message || "Unable to load task flow.";
  }
}

function openInitialBatchWindowFromLocation() {
  const path = window.location.pathname;
  if (path === "/console/batches") {
    openBatchWindow();
    return;
  }
  const match = path.match(/^\/console\/batches\/([^/]+)$/);
  if (match) {
    openBatchWindow(decodeURIComponent(match[1]));
    return;
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("window") === "batches") {
    openBatchWindow(params.get("batch_id") || "");
  }
}

function formatJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function formatDetailValue(value) {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  return value;
}

function formatBoolean(value) {
  return value ? "Yes" : "No";
}

function retrySelectedAgent() {
  if (!selectedAgentId.value) {
    return;
  }
  viewAgent({ id: selectedAgentId.value });
}

function populateEditForm(agentDetail) {
  editForm.value = {
    roleName: agentDetail.role_name || "",
    enabled: Boolean(agentDetail.enabled),
    version: agentDetail.version || "",
    timeoutSeconds: agentDetail.timeout_seconds || 300,
    maxRetries: agentDetail.max_retries ?? 0,
  };
}

async function viewAgent(agent) {
  const requestedAgentId = agent.id;
  selectedAgentId.value = requestedAgentId;
  selectedAgentDetail.value = null;
  detailLoading.value = true;
  detailError.value = "";
  isRoleDetailOpen.value = true;

  try {
    const response = await fetch(`/agents/${encodeURIComponent(requestedAgentId)}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (selectedAgentId.value === requestedAgentId && isRoleDetailOpen.value) {
      selectedAgentDetail.value = payload;
    }
  } catch (error) {
    if (selectedAgentId.value === requestedAgentId && isRoleDetailOpen.value) {
      detailError.value = error.message || "Unable to load agent role detail.";
    }
  } finally {
    if (selectedAgentId.value === requestedAgentId && isRoleDetailOpen.value) {
      detailLoading.value = false;
    }
  }
}

async function editAgent(agent) {
  const requestedAgentId = agent.id;
  editingAgentId.value = requestedAgentId;
  editForm.value = createEmptyEditForm();
  editLoading.value = true;
  editSaving.value = false;
  editError.value = "";
  editMessage.value = "";
  isRoleEditOpen.value = true;

  try {
    const response = await fetch(`/agents/${encodeURIComponent(requestedAgentId)}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (editingAgentId.value === requestedAgentId && isRoleEditOpen.value) {
      populateEditForm(payload);
    }
  } catch (error) {
    if (editingAgentId.value === requestedAgentId && isRoleEditOpen.value) {
      editError.value = error.message || "Unable to load agent role for editing.";
    }
  } finally {
    if (editingAgentId.value === requestedAgentId && isRoleEditOpen.value) {
      editLoading.value = false;
    }
  }
}

async function saveAgentEdit() {
  if (!editingAgentId.value) {
    return;
  }

  editSaving.value = true;
  editError.value = "";
  editMessage.value = "";

  try {
    const timeoutSeconds = Number(editForm.value.timeoutSeconds);
    const maxRetries = Number(editForm.value.maxRetries);
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) {
      throw new Error("Timeout seconds must be greater than 0.");
    }
    if (!Number.isFinite(maxRetries) || maxRetries < 0) {
      throw new Error("Max retries must be 0 or greater.");
    }

    const payload = {
      timeout_seconds: timeoutSeconds,
      max_retries: maxRetries,
      enabled: editForm.value.enabled,
      version: editForm.value.version,
    };

    const requestedAgentId = editingAgentId.value;
    const response = await fetch(`/agents/${encodeURIComponent(requestedAgentId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(errorPayload.detail || `Update failed with status ${response.status}`);
    }
    const updatedAgent = await response.json();
    editMessage.value = "Role updated.";
    populateEditForm(updatedAgent);
    if (selectedAgentId.value === requestedAgentId && isRoleDetailOpen.value) {
      selectedAgentDetail.value = updatedAgent;
    }
    await loadRegistry();
  } catch (error) {
    editError.value = error.message || "Unable to update agent role.";
  } finally {
    editSaving.value = false;
  }
}

function handleKeydown(event) {
  if (event.key !== "Escape") {
    return;
  }
  if (isBatchWindowOpen.value) {
    closeBatchWindow();
    return;
  }
  if (isRoleDetailOpen.value) {
    closeRoleDetail();
    return;
  }
  if (isRoleEditOpen.value) {
    closeRoleEdit();
    return;
  }
  if (isDrawerOpen.value) {
    closeDrawer();
    return;
  }
  if (isSideMenuOpen.value) {
    closeSideMenu();
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

  const normalizedTaskType = "planner_preprocess";
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
    await loadRegistry();
  } catch (error) {
    submitMessage.value = error.message || "Unable to submit task batch.";
  } finally {
    submitting.value = false;
  }
}

async function loadRegistry() {
  loading.value = true;
  errorMessage.value = "";
  statusText.value = "Loading agent registry...";

  try {
    const response = await fetch("/agents/registry");
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    agents.value = payload.items || [];
    statusText.value = `${agents.value.length} role${agents.value.length === 1 ? "" : "s"} loaded`;
  } catch (error) {
    agents.value = [];
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
  openInitialBatchWindowFromLocation();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleKeydown);
  document.body.classList.remove("drawer-locked");
  document.body.classList.remove("modal-locked");
});
</script>

<template>
  <main class="registry-shell" :class="{ 'side-menu-expanded': isSideMenuOpen }">
    <nav class="console-side-nav" :class="{ expanded: isSideMenuOpen }" aria-label="Console navigation">
      <button
        class="side-nav-toggle"
        type="button"
        aria-controls="console-side-nav-list"
        :aria-expanded="isSideMenuOpen"
        :aria-label="isSideMenuOpen ? 'Collapse console menu' : 'Expand console menu'"
        @click="toggleSideMenu"
      >
        Menu
      </button>
      <div v-if="isSideMenuOpen" id="console-side-nav-list" class="side-nav-list">
        <button class="side-nav-item" type="button" @click="openBatchWindow()">Batch Console</button>
        <button class="side-nav-item" type="button" @click="openRolesFromSideMenu">Open Agent Roles</button>
      </div>
    </nav>

    <section class="hero-panel">
      <div class="hero-orb hero-orb-one"></div>
      <div class="hero-orb hero-orb-two"></div>
      <div class="hero-copy">
        <p class="eyebrow">AI Agent Control Center</p>
        <h1>Task <span>Forge</span></h1>
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
          <button
            v-if="submittedBatchId"
            class="inline-link-button"
            type="button"
            @click="openBatchWindow(submittedBatchId)"
          >
            Open batch
          </button>
        </p>
      </div>
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
          <article
            v-for="agent in filteredAgents"
            :key="agent.id"
            class="role-item"
            :class="{ selected: (isRoleDetailOpen && selectedAgentId === agent.id) || (isRoleEditOpen && editingAgentId === agent.id) }"
          >
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
              <button
                class="drawer-primary"
                type="button"
                :disabled="detailLoading && selectedAgentId === agent.id"
                @click="viewAgent(agent)"
              >
                {{
                  detailLoading && selectedAgentId === agent.id
                    ? "Loading"
                    : selectedAgentId === agent.id
                      ? "Viewing"
                      : "View"
                }}
              </button>
              <button
                type="button"
                :disabled="editLoading && editingAgentId === agent.id"
                @click="editAgent(agent)"
              >
                {{
                  editLoading && editingAgentId === agent.id
                    ? "Loading"
                    : editingAgentId === agent.id && isRoleEditOpen
                      ? "Editing"
                      : "Edit"
                }}
              </button>
            </div>
          </article>

          <article v-if="!filteredAgents.length" class="drawer-empty">
            No matching agent roles.
          </article>
        </section>
      </aside>
    </Transition>

    <Transition name="slide">
      <aside
        v-if="isRoleDetailOpen || isRoleEditOpen"
        class="role-side-panel"
        aria-label="Role detail panel"
        aria-live="polite"
      >
        <section v-if="isRoleDetailOpen" class="role-detail-panel">
          <header class="role-detail-hero">
            <div class="role-detail-title">
              <p class="section-label">Role detail</p>
              <h3>{{ selectedAgentDetail?.role_name || "Loading role" }}</h3>
              <p v-if="selectedAgentDetail" class="role-detail-description">
                {{ selectedAgentDetail.description || "No description" }}
              </p>
            </div>
            <button class="icon-button" type="button" aria-label="Close role detail" @click="closeRoleDetail">x</button>
          </header>

          <div v-if="detailLoading" class="detail-state detail-state-loading">
            <span class="detail-loading-dot"></span>
            <p>Loading role detail...</p>
          </div>
          <div v-else-if="detailError" class="detail-state detail-state-error">
            <p>{{ detailError }}</p>
            <button class="ghost-button compact-action" type="button" @click="retrySelectedAgent">Retry</button>
          </div>

          <div v-else-if="selectedAgentDetail" class="role-detail-body">
            <div class="role-detail-badges">
              <span class="status-badge" :class="selectedAgentDetail.enabled ? 'enabled' : 'disabled'">
                {{ selectedAgentDetail.enabled ? "Enabled" : "Disabled" }}
              </span>
              <span class="version-pill">v{{ selectedAgentDetail.version }}</span>
              <span class="meta-pill">{{ selectedAgentDetail.timeout_seconds }}s timeout</span>
              <span class="meta-pill">{{ selectedAgentDetail.max_retries }} retries</span>
            </div>

            <section class="detail-card-grid">
              <article class="detail-card">
                <p class="detail-card-label">Supported task types</p>
                <div class="pill-row">
                  <span v-for="taskTypeName in roleTaskTypes(selectedAgentDetail)" :key="taskTypeName" class="meta-pill">
                    {{ taskTypeName }}
                  </span>
                </div>
              </article>

              <article class="detail-card">
                <p class="detail-card-label">Capabilities</p>
                <div class="pill-row">
                  <span v-for="capability in selectedAgentDetail.capabilities || []" :key="capability" class="meta-pill">
                    {{ capability }}
                  </span>
                  <span v-if="!(selectedAgentDetail.capabilities || []).length" class="muted">None</span>
                </div>
              </article>

              <article class="detail-card">
                <p class="detail-card-label">Runtime</p>
                <dl class="detail-kv-grid">
                  <div><dt>Timeout</dt><dd>{{ selectedAgentDetail.timeout_seconds }}s</dd></div>
                  <div><dt>Retries</dt><dd>{{ selectedAgentDetail.max_retries }}</dd></div>
                  <div>
                    <dt>Concurrency</dt>
                    <dd>{{ formatBoolean(selectedAgentDetail.capability_declaration?.supports_concurrency) }}</dd>
                  </div>
                  <div>
                    <dt>Auto retry</dt>
                    <dd>{{ formatBoolean(selectedAgentDetail.capability_declaration?.allows_auto_retry) }}</dd>
                  </div>
                </dl>
              </article>

              <article class="detail-card">
                <p class="detail-card-label">Prompt budget</p>
                <dl class="detail-kv-grid">
                  <div v-for="[label, field] in promptBudgetHighlights" :key="field">
                    <dt>{{ label }}</dt>
                    <dd>{{ formatDetailValue(selectedAgentDetail.prompt_budget_policy?.[field]) }}</dd>
                  </div>
                </dl>
              </article>
            </section>

            <section class="advanced-config">
              <div class="advanced-config-header">
                <p class="section-label">Advanced configuration</p>
              </div>

              <details class="detail-json-block" open>
                <summary>Capability declaration</summary>
                <pre>{{ formatJson(selectedAgentDetail.capability_declaration) }}</pre>
              </details>

              <details class="detail-json-block">
                <summary>Prompt budget policy</summary>
                <pre>{{ formatJson(selectedAgentDetail.prompt_budget_policy) }}</pre>
              </details>

              <details class="detail-json-block">
                <summary>Input schema</summary>
                <pre>{{ formatJson(selectedAgentDetail.input_schema) }}</pre>
              </details>

              <details class="detail-json-block">
                <summary>Output schema</summary>
                <pre>{{ formatJson(selectedAgentDetail.output_schema) }}</pre>
              </details>
            </section>
          </div>
        </section>

        <section v-if="isRoleEditOpen" class="role-edit-panel">
          <header class="role-edit-header">
            <div>
              <p class="section-label">Edit role</p>
              <h3>{{ editForm.roleName || "Loading role" }}</h3>
              <p class="role-edit-note">Only version, status, timeout, and retry policy can be edited here.</p>
            </div>
            <button class="icon-button" type="button" aria-label="Close role editor" @click="closeRoleEdit">x</button>
          </header>

          <div v-if="editLoading" class="detail-state detail-state-loading">
            <span class="detail-loading-dot"></span>
            <p>Loading editable role fields...</p>
          </div>

          <form v-else class="role-edit-form" @submit.prevent="saveAgentEdit">
            <p v-if="editError" class="detail-state detail-state-error">{{ editError }}</p>
            <p v-if="editMessage" class="edit-success-message">{{ editMessage }}</p>

            <div class="edit-form-grid">
              <label class="field">
                <span>Version</span>
                <input v-model="editForm.version" type="text" placeholder="1.0.0">
              </label>

              <label class="field">
                <span>Status</span>
                <select v-model="editForm.enabled">
                  <option :value="true">Enabled</option>
                  <option :value="false">Disabled</option>
                </select>
              </label>

              <label class="field">
                <span>Timeout seconds</span>
                <input v-model.number="editForm.timeoutSeconds" type="number" min="1" step="1">
              </label>

              <label class="field">
                <span>Max retries</span>
                <input v-model.number="editForm.maxRetries" type="number" min="0" step="1">
              </label>
            </div>

            <div class="edit-actions">
              <button class="ghost-button" type="button" :disabled="editSaving" @click="closeRoleEdit">Cancel</button>
              <button class="primary-button compact" type="submit" :disabled="editSaving">
                {{ editSaving ? "Saving" : "Save changes" }}
              </button>
            </div>
          </form>
        </section>
      </aside>
    </Transition>

    <Transition name="fade">
      <button
        v-if="isBatchWindowOpen"
        class="batch-window-overlay"
        type="button"
        aria-label="Close batch console"
        @click="closeBatchWindow"
      ></button>
    </Transition>

    <Transition name="modal">
      <div
        v-if="isBatchWindowOpen"
        class="batch-console-layout"
        role="dialog"
        aria-modal="true"
        aria-labelledby="batch-window-title"
      >
        <section
          class="batch-window"
        >
          <header class="batch-window-header">
            <div>
              <p class="section-label">Batch console</p>
              <h2 id="batch-window-title">Batches</h2>
            </div>
            <button class="icon-button" type="button" aria-label="Close batch console" @click="closeBatchWindow">x</button>
          </header>

          <section class="batch-window-toolbar" aria-label="Batch filters">
            <label class="field">
              <span>Search</span>
              <input v-model="batchSearch" type="search" placeholder="Find batch by title" @input="loadBatches">
            </label>
            <label class="field">
              <span>Status</span>
              <select v-model="batchStatusFilter" @change="loadBatches">
                <option v-for="option in batchStatusOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
            </label>
            <label class="field">
              <span>Sort</span>
              <select v-model="batchSort" @change="loadBatches">
                <option v-for="option in batchSortOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
            </label>
            <button class="primary-button compact" type="button" :disabled="batchLoading" @click="loadBatches">
              {{ batchLoading ? "Loading" : "Refresh" }}
            </button>
          </section>

          <section class="batch-status-line" aria-live="polite">
            <span :class="{ pulse: batchLoading }"></span>
            <p>{{ batchStatusText }}</p>
          </section>

          <p v-if="batchError" class="error-banner batch-error">{{ batchError }}</p>

          <section class="batch-window-body">
            <aside class="batch-window-list" aria-label="Task batches">
              <article v-if="!batches.length && !batchLoading" class="batch-empty-state">
                No batches matched the current filters.
              </article>

            <button
              v-for="batch in batches"
              :key="batch.batch_id"
              class="batch-window-card batch-window-card-button"
              :class="{ selected: selectedBatchId === batch.batch_id }"
              type="button"
              :aria-pressed="selectedBatchId === batch.batch_id"
              @click="openBatchDetail(batch.batch_id)"
            >
              <span class="batch-card-head">
                <span class="batch-card-title-block">
                  <strong class="batch-card-title">{{ batch.title }}</strong>
                  <small class="batch-card-id">{{ batch.batch_id }}</small>
                </span>
                <span class="status-badge" :class="`status-${batch.derived_status}`">
                  {{ batch.derived_status }}
                </span>
              </span>
              <span class="batch-card-metrics" aria-label="Batch metrics">
                <span><small>Total</small><strong>{{ batch.total_tasks }}</strong></span>
                <span><small>Done</small><strong>{{ batch.completed_count }}</strong></span>
                <span><small>Success</small><strong>{{ batch.success_rate }}%</strong></span>
              </span>
              <span class="batch-card-footer">
                <small>Updated {{ formatDate(batch.updated_at) }}</small>
                <span class="batch-card-open-indicator" aria-hidden="true">&gt;</span>
              </span>
            </button>
            </aside>
          </section>
        </section>
        <aside class="task-detail-dock" aria-label="Task Detail">
          <header class="task-detail-dock-header">
            <div>
              <p class="section-label">Task Detail</p>
              <h2>{{ selectedBatchTask?.title || "Task Detail" }}</h2>
            </div>
            <button
              v-if="selectedBatchTask"
              class="icon-button"
              type="button"
              aria-label="Clear task detail"
              @click="closeBatchTaskDetail"
            >
              x
            </button>
          </header>

          <section v-if="selectedBatchId && !selectedBatchSummary" class="task-detail-empty">
            Loading task details...
          </section>

          <section v-else-if="selectedBatchSummary && !selectedBatchTask" class="task-detail-empty">
            Selected batch has no tasks
          </section>

          <section v-else-if="!selectedBatchTask" class="task-detail-empty">
            Select a batch to view task details
          </section>

          <section v-else class="task-detail-dock-body">
            <section class="batch-detail-section">
              <div class="batch-section-heading">
                <p class="section-label">Task flow</p>
                <h4>{{ selectedBatchTask.task_type || "Task" }}</h4>
              </div>
              <article v-if="!selectedTaskTimelineItems.length" class="batch-detail-state">No flow events available yet.</article>
              <div v-else class="batch-flow-list">
                <article
                  v-for="item in selectedTaskTimelineItems"
                  :key="`${item.stage}-${item.timestamp}-${item.run_id || ''}`"
                  class="batch-flow-step"
                  :class="item.stage || 'status'"
                >
                  <div class="batch-flow-marker"></div>
                  <div class="batch-flow-card">
                    <div class="batch-flow-card-head">
                      <strong>{{ flowStageLabel(item.stage) }}</strong>
                      <span>{{ formatDate(item.timestamp) }}</span>
                    </div>
                    <h5>{{ item.title }}</h5>
                    <p>{{ item.detail || "No additional detail." }}</p>
                    <div class="batch-flow-meta">
                      <span>actor {{ item.actor || "system" }}</span>
                      <span>run {{ item.run_id || "n/a" }}</span>
                    </div>
                  </div>
                </article>
              </div>
            </section>

            <section class="batch-detail-section">
              <div class="batch-section-heading">
                <p class="section-label">Deliverables</p>
                <h4>Selected task outputs</h4>
              </div>
              <article v-if="selectedTaskMissingFileDeliverable" class="batch-detail-state warning">
                This code task did not produce file-level deliverables.
              </article>
              <article v-if="!selectedTaskArtifacts.length" class="batch-detail-state">
                No deliverables for the selected task.
              </article>
              <article
                v-for="artifact in selectedTaskArtifacts"
                :key="artifact.artifact_id || `${artifact.task_id}-${artifact.run_id}-${artifact.artifact_type}`"
                class="batch-artifact-card"
              >
                <div class="batch-artifact-head">
                  <strong>{{ artifactTitle(artifact) }}</strong>
                  <span>{{ artifactDeliverableLabel(artifact) }}</span>
                </div>
                <div class="batch-artifact-actions">
                  <span>{{ artifact.content_type || artifact.artifact_type || "unknown" }}</span>
                  <button class="artifact-download-button" type="button" @click="downloadArtifact(artifact)">
                    Download
                  </button>
                </div>
                <p class="batch-artifact-meta">
                  {{ artifact.uri || "No URI" }} /
                  task {{ artifact.task_id || "n/a" }} /
                  run {{ artifact.run_id || "n/a" }}
                </p>
                <pre v-if="artifactPreview(artifact)">{{ artifactPreview(artifact) }}</pre>
                <details v-if="fullArtifactContent(artifact)" class="batch-artifact-full">
                  <summary>Full content</summary>
                  <pre>{{ fullArtifactContent(artifact) }}</pre>
                </details>
                <details class="batch-artifact-full">
                  <summary>Structured output</summary>
                  <pre>{{ formatJson(artifact.structured_output) }}</pre>
                </details>
              </article>
            </section>
          </section>
        </aside>
      </div>
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

:global(body.modal-locked) {
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
  --side-nav-offset: clamp(-280px, calc((1280px - 100vw) / 2), 0px);

  position: relative;
  isolation: isolate;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 16px;
  width: min(1280px, calc(100% - 32px));
  margin: 0 auto;
  padding: 42px 0 64px;
  transition: grid-template-columns 180ms ease;
}

.registry-shell.side-menu-expanded {
  grid-template-columns: 224px minmax(0, 1fr);
}

.registry-shell > :not(.console-side-nav) {
  grid-column: 2;
  min-width: 0;
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

.hero-panel,
.task-submit-panel,
.panel,
.role-drawer,
.role-side-panel,
.console-side-nav,
.batch-window,
.task-detail-dock {
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
  grid-template-columns: minmax(0, 1fr);
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

.hero-copy {
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
  margin-bottom: 12px;
  padding: 0.04em 0.12em 0.16em 0;
  overflow: visible;
  color: #f8fafc;
  font-size: clamp(3.2rem, 8vw, 7.4rem);
  line-height: 1.16;
  letter-spacing: 0;
}

h1 span {
  display: inline-block;
  padding: 0.04em 0.12em 0.14em 0;
  overflow: visible;
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

.role-actions,
.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.primary-button,
.ghost-button,
.ghost-link,
.inline-link-button,
.role-actions button,
.icon-button,
.side-nav-toggle,
.side-nav-item {
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
.inline-link-button,
.role-actions button,
.icon-button,
.side-nav-toggle,
.side-nav-item {
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.035);
  color: #e2e8f0;
  padding: 0 16px;
}

.ghost-link {
  color: #cbd5e1;
}

.inline-link-button {
  min-height: 0;
  border: 0;
  background: transparent;
  color: #c4b5fd;
  font-size: inherit;
  font-weight: 800;
  padding: 0;
}

.primary-button:hover,
.ghost-button:hover,
.ghost-link:hover,
.inline-link-button:hover,
.role-actions button:hover,
.icon-button:hover,
.side-nav-toggle:hover,
.side-nav-item:hover {
  border-color: rgba(167, 139, 250, 0.62);
  box-shadow: 0 0 32px rgba(139, 92, 246, 0.18);
  transform: translateY(-1px);
}

.side-nav-toggle:focus-visible,
.side-nav-item:focus-visible {
  outline: 2px solid rgba(196, 181, 253, 0.86);
  outline-offset: 3px;
}

.console-side-nav {
  position: sticky;
  top: 42px;
  z-index: 10;
  align-self: start;
  display: grid;
  gap: 12px;
  min-height: 64px;
  padding: 8px;
  overflow: hidden;
  border-radius: 22px;
  transform: translateX(var(--side-nav-offset));
  transition: transform 180ms ease, border-color 160ms ease, box-shadow 160ms ease;
  background:
    radial-gradient(circle at 0% 0%, rgba(139, 92, 246, 0.2), transparent 44%),
    rgba(18, 18, 24, 0.84);
}

.console-side-nav.expanded {
  border-color: rgba(139, 92, 246, 0.36);
  box-shadow:
    0 24px 80px rgba(0, 0, 0, 0.45),
    0 0 32px rgba(139, 92, 246, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.side-nav-toggle {
  width: 48px;
  min-height: 48px;
  padding: 0;
  color: #f8fafc;
  font-size: 0.78rem;
  font-weight: 800;
}

.side-nav-list {
  display: grid;
  gap: 10px;
}

.side-nav-item {
  width: 100%;
  min-height: 44px;
  justify-content: flex-start;
  border-radius: 12px;
  color: #cbd5e1;
  font-size: 0.9rem;
  font-weight: 800;
  text-align: left;
}

.side-nav-item:is(button) {
  text-align: left;
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
.field select,
.field textarea {
  width: 100%;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  outline: none;
  background: rgba(0, 0, 0, 0.35);
  color: #f8fafc;
  text-transform: none;
  transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.field input,
.field select {
  height: 46px;
  padding: 0 14px;
}

.field textarea {
  min-height: 92px;
  resize: vertical;
  padding: 12px 14px;
  text-transform: none;
  line-height: 1.5;
}

.field input::placeholder,
.field textarea::placeholder {
  color: #64748b;
}

.field input:focus,
.field select:focus,
.field textarea:focus {
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
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}

.panel {
  border-radius: 24px;
  padding: 22px;
  transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}

.panel:hover {
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

.status-success {
  border: 1px solid rgba(167, 139, 250, 0.34);
  background: rgba(139, 92, 246, 0.14);
  color: #ddd6fe;
}

.status-failed,
.status-partially_failed {
  border: 1px solid rgba(251, 113, 133, 0.34);
  background: rgba(244, 63, 94, 0.12);
  color: #fb7185;
}

.status-running {
  border: 1px solid rgba(147, 197, 253, 0.34);
  background: rgba(59, 130, 246, 0.13);
  color: #93c5fd;
}

.status-needs_review {
  border: 1px solid rgba(251, 191, 36, 0.34);
  background: rgba(245, 158, 11, 0.13);
  color: #fbbf24;
}

.status-pending,
.status-cancelled {
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(100, 116, 139, 0.15);
  color: #94a3b8;
}

.batch-window-overlay {
  position: fixed;
  inset: 0;
  z-index: 45;
  border: 0;
  background:
    radial-gradient(circle at 50% 18%, rgba(139, 92, 246, 0.16), transparent 34%),
    rgba(0, 0, 0, 0.72);
}

.batch-console-layout {
  position: fixed;
  top: 50%;
  left: 50%;
  z-index: 50;
  display: grid;
  grid-template-columns: minmax(360px, 520px) clamp(420px, 30vw, 520px);
  gap: 24px;
  width: min(1064px, calc(100vw - 40px));
  height: min(760px, calc(100vh - 40px));
  min-width: 0;
  transform: translate(-50%, -50%);
}

.batch-window {
  position: relative;
  display: grid;
  grid-template-rows: auto auto auto auto minmax(0, 1fr);
  width: 100%;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  border-color: rgba(167, 139, 250, 0.34);
  border-radius: 24px;
  background:
    radial-gradient(circle at 32% 0%, rgba(139, 92, 246, 0.18), transparent 38%),
    rgba(12, 12, 18, 0.97);
  box-shadow:
    0 32px 100px rgba(0, 0, 0, 0.62),
    0 0 44px rgba(139, 92, 246, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.task-detail-dock {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  border-color: rgba(167, 139, 250, 0.34);
  border-radius: 24px;
  background:
    radial-gradient(circle at 26% 0%, rgba(139, 92, 246, 0.18), transparent 38%),
    rgba(12, 12, 18, 0.97);
  box-shadow:
    0 32px 100px rgba(0, 0, 0, 0.5),
    0 0 44px rgba(139, 92, 246, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.task-detail-dock-header {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.task-detail-dock-header h2 {
  margin-top: 4px;
  overflow-wrap: anywhere;
}

.task-detail-dock-body {
  display: grid;
  align-content: start;
  gap: 12px;
  min-height: 0;
  overflow: auto;
  padding: 16px;
}

.task-detail-empty {
  display: grid;
  place-items: center;
  min-height: 0;
  color: #94a3b8;
  line-height: 1.5;
  padding: 24px;
  text-align: center;
}

.batch-window-header,
.batch-window-toolbar {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.batch-window-header {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
}

.batch-window-header h2 {
  margin-top: 4px;
}

.batch-window-toolbar {
  grid-template-columns: minmax(0, 1.3fr) minmax(150px, 0.8fr) minmax(180px, 0.9fr) auto;
  align-items: end;
}

.batch-status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  color: #94a3b8;
  padding: 0 16px;
}

.batch-status-line p {
  margin: 0;
}

.batch-status-line span {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #a78bfa;
  box-shadow: 0 0 18px rgba(167, 139, 250, 0.8);
}

.batch-error {
  margin: 0 16px 12px;
}

.batch-window-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
  overflow: hidden;
  padding: 0 16px 16px;
}

.batch-window-list {
  min-height: 0;
  overflow: auto;
}

.batch-window-list {
  display: grid;
  align-content: start;
  gap: 10px;
}

.batch-window-card,
.batch-empty-state,
.batch-detail-state,
.batch-detail-section,
.batch-artifact-card {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.04);
  padding: 12px;
}

.batch-window-card {
  position: relative;
  display: grid;
  gap: 12px;
  overflow: hidden;
  transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}

.batch-window-card-button {
  width: 100%;
  min-height: 156px;
  color: #f8fafc;
  text-align: left;
  padding: 14px 14px 12px 16px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.025)),
    rgba(255, 255, 255, 0.035);
}

.batch-window-card-button:hover,
.batch-window-card-button:focus-visible {
  border-color: rgba(167, 139, 250, 0.58);
  background:
    linear-gradient(135deg, rgba(139, 92, 246, 0.13), rgba(255, 255, 255, 0.04)),
    rgba(255, 255, 255, 0.045);
  box-shadow: 0 0 26px rgba(139, 92, 246, 0.14);
  outline: none;
  transform: translateY(-1px);
}

.batch-window-card-button:focus-visible {
  box-shadow:
    0 0 0 3px rgba(196, 181, 253, 0.22),
    0 0 26px rgba(139, 92, 246, 0.14);
}

.batch-window-card-button.selected {
  border-color: rgba(167, 139, 250, 0.72);
  background:
    linear-gradient(90deg, rgba(139, 92, 246, 0.18), rgba(255, 255, 255, 0.045) 44%),
    rgba(139, 92, 246, 0.08);
  box-shadow:
    inset 3px 0 0 rgba(167, 139, 250, 0.88),
    0 0 28px rgba(139, 92, 246, 0.13);
}

.batch-card-head,
.batch-card-footer,
.batch-artifact-head,
.batch-flow-card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.batch-card-title-block {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.batch-card-title {
  color: #f8fafc;
  font-size: 0.98rem;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.batch-card-id,
.batch-card-footer small,
.batch-card-metrics small,
.batch-artifact-meta {
  margin: 0;
  color: #94a3b8;
  font-size: 0.76rem;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.batch-card-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}

.batch-card-metrics span {
  min-width: 0;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.18);
  padding: 9px;
}

.batch-card-metrics strong {
  display: block;
  margin-top: 5px;
  color: #f8fafc;
  font-size: 0.98rem;
}

.batch-card-open-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border: 1px solid rgba(167, 139, 250, 0.26);
  border-radius: 999px;
  background: rgba(139, 92, 246, 0.1);
  color: #ddd6fe;
  font-size: 1.15rem;
  line-height: 1;
  transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
}

.batch-window-card-button:hover .batch-card-open-indicator,
.batch-window-card-button:focus-visible .batch-card-open-indicator,
.batch-window-card-button.selected .batch-card-open-indicator {
  border-color: rgba(196, 181, 253, 0.62);
  background: rgba(139, 92, 246, 0.22);
  transform: translateX(2px);
}

.batch-detail-section {
  display: grid;
  gap: 12px;
}

.batch-section-heading h4 {
  margin: 4px 0 0;
}

.batch-flow-list,
.batch-artifact-full {
  display: grid;
  gap: 10px;
}

.batch-flow-step {
  position: relative;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 10px;
}

.batch-flow-step::before {
  position: absolute;
  top: 16px;
  bottom: -14px;
  left: 8px;
  width: 1px;
  background: rgba(255, 255, 255, 0.1);
  content: "";
}

.batch-flow-step:last-child::before {
  display: none;
}

.batch-flow-marker {
  position: relative;
  z-index: 1;
  width: 17px;
  height: 17px;
  margin-top: 12px;
  border: 3px solid rgba(167, 139, 250, 0.72);
  border-radius: 999px;
  background: #0c0c12;
  box-shadow: 0 0 18px rgba(139, 92, 246, 0.4);
}

.batch-flow-card {
  min-width: 0;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.2);
  padding: 10px;
}

.batch-flow-card-head,
.batch-flow-meta,
.batch-artifact-head span {
  color: #94a3b8;
  font-size: 0.78rem;
}

.batch-artifact-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #94a3b8;
  font-size: 0.78rem;
}

.artifact-download-button {
  min-height: 30px;
  border: 1px solid rgba(167, 139, 250, 0.28);
  border-radius: 999px;
  background: rgba(139, 92, 246, 0.12);
  color: #ddd6fe;
  cursor: pointer;
  font: inherit;
  font-weight: 800;
  padding: 6px 10px;
}

.artifact-download-button:hover,
.artifact-download-button:focus-visible {
  border-color: rgba(196, 181, 253, 0.62);
  background: rgba(139, 92, 246, 0.22);
  outline: none;
}

.batch-flow-card h5 {
  margin: 8px 0 6px;
  color: #f8fafc;
}

.batch-flow-card p,
.batch-flow-meta {
  margin: 0;
  color: #94a3b8;
  line-height: 1.45;
}

.batch-flow-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.batch-artifact-card {
  display: grid;
  gap: 10px;
}

.batch-artifact-head strong {
  overflow-wrap: anywhere;
}

.batch-artifact-card pre,
.batch-artifact-full pre {
  max-height: 220px;
  margin: 0;
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.28);
  color: #dbeafe;
  font-size: 0.78rem;
  line-height: 1.5;
  padding: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}

.batch-artifact-full summary {
  cursor: pointer;
  color: #ddd6fe;
  font-weight: 800;
}

.batch-detail-state {
  color: #94a3b8;
}

.batch-detail-state.warning {
  border-color: rgba(251, 191, 36, 0.34);
  background: rgba(251, 191, 36, 0.1);
  color: #fbbf24;
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

.role-side-panel {
  position: fixed;
  top: 16px;
  right: 16px;
  bottom: 16px;
  z-index: 40;
  width: min(760px, calc(100vw - 32px));
  overflow-y: auto;
  border-color: rgba(167, 139, 250, 0.34);
  border-radius: 28px;
  background:
    radial-gradient(circle at 32% 0%, rgba(139, 92, 246, 0.2), transparent 38%),
    rgba(12, 12, 18, 0.96);
  box-shadow:
    -28px 0 90px rgba(0, 0, 0, 0.56),
    0 0 44px rgba(139, 92, 246, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
  padding: 18px;
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

.role-detail-panel {
  display: grid;
  gap: 16px;
  margin-top: 16px;
  border: 1px solid rgba(167, 139, 250, 0.34);
  border-radius: 20px;
  background:
    radial-gradient(circle at 0% 0%, rgba(139, 92, 246, 0.18), transparent 42%),
    rgba(255, 255, 255, 0.055);
  padding: 16px;
}

.role-side-panel .role-detail-panel,
.role-side-panel .role-edit-panel {
  margin-top: 0;
}

.role-edit-panel {
  display: grid;
  gap: 16px;
  margin-top: 16px;
  border: 1px solid rgba(129, 140, 248, 0.34);
  border-radius: 20px;
  background:
    linear-gradient(135deg, rgba(99, 102, 241, 0.14), rgba(255, 255, 255, 0.04)),
    rgba(255, 255, 255, 0.055);
  padding: 16px;
}

.role-edit-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.role-edit-header h3 {
  margin-bottom: 0;
  overflow-wrap: anywhere;
}

.role-edit-note {
  margin: 8px 0 0;
  color: #94a3b8;
  line-height: 1.5;
}

.role-edit-form {
  display: grid;
  gap: 14px;
}

.edit-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.edit-success-message {
  margin: 0;
  border: 1px solid rgba(34, 197, 94, 0.28);
  border-radius: 14px;
  background: rgba(34, 197, 94, 0.08);
  color: #bbf7d0;
  padding: 12px;
}

.edit-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.role-detail-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(139, 92, 246, 0.18), rgba(255, 255, 255, 0.035)),
    rgba(0, 0, 0, 0.18);
  padding: 14px;
}

.role-detail-title {
  min-width: 0;
}

.role-detail-title h3 {
  margin-bottom: 0;
  overflow-wrap: anywhere;
}

.role-detail-description {
  margin: 0;
  color: #94a3b8;
  line-height: 1.55;
}

.role-detail-description {
  margin-top: 8px;
}

.detail-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.18);
  color: #94a3b8;
  padding: 12px;
}

.detail-state p {
  margin: 0;
}

.detail-state-loading {
  justify-content: flex-start;
}

.detail-state-error {
  border-color: rgba(244, 63, 94, 0.32);
  background: rgba(244, 63, 94, 0.08);
  color: #fecdd3;
}

.detail-loading-dot {
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  border-radius: 999px;
  background: #a78bfa;
  box-shadow: 0 0 18px rgba(167, 139, 250, 0.78);
  animation: pulse 900ms ease-in-out infinite alternate;
}

.compact-action {
  min-height: 36px;
  padding: 0 12px;
  white-space: nowrap;
}

.role-detail-body {
  display: grid;
  gap: 14px;
}

.role-detail-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.detail-card {
  display: grid;
  align-content: start;
  gap: 10px;
  min-width: 0;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.045);
  padding: 12px;
}

.detail-card-label {
  margin: 0;
  color: #a78bfa;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.detail-kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}

.detail-kv-grid div {
  min-width: 0;
}

.detail-kv-grid dt {
  color: #64748b;
  font-size: 0.72rem;
}

.detail-kv-grid dd {
  margin: 5px 0 0;
  color: #f8fafc;
  font-size: 0.92rem;
  font-weight: 800;
  overflow-wrap: anywhere;
}

.advanced-config {
  display: grid;
  gap: 10px;
}

.advanced-config-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-json-block {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.2);
  color: #cbd5e1;
  overflow: hidden;
}

.detail-json-block summary {
  cursor: pointer;
  color: #ddd6fe;
  font-size: 0.84rem;
  font-weight: 800;
  padding: 11px 12px;
  transition: background 160ms ease, color 160ms ease;
}

.detail-json-block summary:hover,
.detail-json-block summary:focus-visible {
  background: rgba(139, 92, 246, 0.1);
  color: #f8fafc;
}

.detail-json-block pre {
  max-height: 220px;
  margin: 0;
  overflow: auto;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: #cbd5e1;
  font-size: 0.78rem;
  line-height: 1.5;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
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

.role-item.selected {
  border-color: rgba(167, 139, 250, 0.62);
  background:
    linear-gradient(90deg, rgba(139, 92, 246, 0.16), rgba(255, 255, 255, 0.045) 34%),
    rgba(139, 92, 246, 0.08);
  box-shadow:
    inset 3px 0 0 rgba(167, 139, 250, 0.82),
    0 0 28px rgba(139, 92, 246, 0.12);
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
.modal-enter-active,
.modal-leave-active {
  transition: opacity 180ms ease, transform 220ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateX(36px);
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
  transform: translate(-50%, -48%) scale(0.98);
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
  .registry-shell,
  .registry-shell.side-menu-expanded {
    display: block;
    width: min(100% - 20px, 1180px);
    padding-top: 82px;
  }

  .console-side-nav {
    position: fixed;
    top: 14px;
    left: 10px;
    width: 64px;
    transform: none;
  }

  .console-side-nav.expanded {
    width: min(260px, calc(100vw - 20px));
  }

  .overview-grid {
    grid-template-columns: 1fr;
  }

  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .batch-window-toolbar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .batch-window-body {
    grid-template-columns: 1fr;
  }

  .batch-console-layout {
    grid-template-columns: minmax(320px, 1fr) minmax(360px, 420px);
    gap: 16px;
  }
}

@media (max-width: 720px) {
  .registry-shell {
    width: min(100% - 20px, 1180px);
  }

  .task-submit-panel,
  .detail-card-grid,
  .drawer-tools,
  .role-metrics,
  .edit-form-grid {
    grid-template-columns: 1fr;
  }

  .detail-kv-grid {
    grid-template-columns: 1fr;
  }

  .role-main {
    flex-direction: column;
  }

  .role-state {
    justify-items: start;
  }

  .batch-console-layout {
    inset: 10px;
    top: auto;
    left: auto;
    width: auto;
    height: calc(100vh - 20px);
    max-height: none;
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 1fr) minmax(280px, 40vh);
    gap: 12px;
    transform: none;
  }

  .batch-window,
  .task-detail-dock {
    border-radius: 18px;
  }

  .batch-window-toolbar,
  .batch-card-metrics {
    grid-template-columns: 1fr;
  }

  .modal-enter-from,
  .modal-leave-to {
    transform: translateY(12px) scale(0.98);
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

  .role-side-panel {
    inset: 0;
    width: 100vw;
    border-radius: 0;
  }
}
</style>
