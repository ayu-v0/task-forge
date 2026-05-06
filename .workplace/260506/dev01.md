# /console/batches 中等窗口改造开发文档

## 开发目标

基于 `task01.md`，将当前独立页面形式的 `/console/batches` 改造为 Agent 控制台内的中等尺寸窗口。

最终用户路径：

1. 用户访问 `/console/agents`。
2. 用户从左侧控制台菜单点击 `Batch Console`。
3. 页面不跳转，在当前 Agent 控制台上打开一个居中的中等窗口。
4. 用户可在窗口内搜索、筛选、排序、刷新批次。
5. 用户点击某个批次后，在同一个窗口内查看该批次详情、任务流和产物。
6. 用户提交任务成功后点击 `Open batch`，打开同一个批次窗口并加载新建批次详情。

## 实现原则

- 新主入口放在 `src/apps/web/vue/src/AgentRegistry.vue`。
- 继续复用现有后端 API，不新增接口。
- 不把旧 `/console/batches` 页面 iframe 嵌入 Vue。
- 不手工编辑压缩后的 `src/apps/web/dist/assets/*`，构建产物由 `npm run build` 生成。
- 旧 HTML/JS/CSS 批次页可以先保留，降低回滚成本。
- 旧 URL 必须继续可访问，不能出现 404。

## 涉及文件

主要开发文件：

- `src/apps/web/vue/src/AgentRegistry.vue`
- `src/apps/api/app.py`
- `src/tests/test_agent_registry_page.py`
- `src/tests/test_task_batch_list.py`

构建产物：

- `src/apps/web/dist/index.html`
- `src/apps/web/dist/assets/*`

暂时保留的旧实现：

- `src/apps/web/index.html`
- `src/apps/web/app.js`
- `src/apps/web/styles.css`
- `src/apps/web/batch-detail.html`
- `src/apps/web/batch-detail.js`
- `src/apps/web/batch-detail.css`

## 前端状态设计

在 `AgentRegistry.vue` 的 `<script setup>` 中新增批次窗口状态。

```js
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
```

建议增加 computed：

```js
const selectedTask = computed(() => {
  return selectedBatchSummary.value?.tasks?.find((task) => task.task_id === selectedTaskId.value) || null;
});
```

状态约束：

- `isBatchWindowOpen` 只控制批次窗口。
- `isDrawerOpen` 继续只控制 Agent Roles 抽屉。
- `isSideMenuOpen` 继续只控制左侧菜单。
- 打开批次窗口时建议关闭侧边菜单、角色抽屉、角色详情面板和角色编辑面板，避免多个浮层重叠。

## API 复用

批次窗口继续使用现有接口：

- 批次列表：`GET /task-batches?search=&status=&sort=`
- 批次详情：`GET /task-batches/{batchId}/summary`
- 任务时间线：`GET /tasks/{taskId}/timeline`

请求行为：

- 每次重新加载批次列表前 abort 上一次列表请求。
- 每次切换批次详情前 abort 上一次详情请求。
- 每次切换任务前 abort 上一次任务时间线请求。
- 关闭批次窗口时统一 abort 所有批次相关请求。

## 方法设计

### 打开窗口

```js
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
```

注意：

- 如果不希望打开批次窗口时关闭角色详情，也可以只关闭 `isSideMenuOpen`，但 Escape 和 z-index 会更复杂。
- `document.body.classList.add("modal-locked")` 可复用现有 `drawer-locked` 样式思路，也可以新增独立 class。

### 关闭窗口

```js
function closeBatchWindow() {
  abortBatchRequests();
  isBatchWindowOpen.value = false;
  selectedBatchId.value = "";
  selectedBatchSummary.value = null;
  selectedTaskId.value = "";
  selectedTaskTimelineItems.value = [];
  document.body.classList.remove("modal-locked");
}
```

### 中止请求

```js
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
```

### 加载批次列表

```js
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
```

### 打开批次详情

```js
async function openBatchDetail(batchId) {
  if (batchDetailAbortController) {
    batchDetailAbortController.abort();
  }
  batchDetailAbortController = new AbortController();

  selectedBatchId.value = batchId;
  selectedBatchSummary.value = null;
  selectedTaskId.value = "";
  selectedTaskTimelineItems.value = [];

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
    selectedTaskId.value = firstTask?.task_id || "";
    if (firstTask) {
      await loadSelectedTaskTimeline(firstTask);
    }
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    batchError.value = error.message || "Unable to load batch detail.";
  }
}
```

### 切换任务时间线

```js
async function selectBatchTask(task) {
  selectedTaskId.value = task.task_id;
  selectedTaskTimelineItems.value = [];
  await loadSelectedTaskTimeline(task);
}
```

```js
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
```

## 工具函数迁移

从 `src/apps/web/app.js` 迁入必要函数，优先保留行为一致：

- `formatDate`
- `previewText`
- `lineCount`
- `generatedPathForLanguage`
- `contentTypeForLanguage`
- `findLegacyCodeResult`
- `inferCodeArtifactsFromJsonArtifacts`
- `selectedTaskLooksLikeCode`
- `artifactPriority`
- `flowStageLabel`

Vue 模板默认会转义插值内容，所以不需要继续大量使用 `escapeHtml`。只有在使用 `v-html` 时才需要手动转义；本实现建议避免 `v-html`。

## 模板结构

### 菜单入口

将侧边菜单中的批次入口从链接改为按钮：

```vue
<button class="side-nav-item" type="button" @click="openBatchWindow()">
  Batch Console
</button>
```

提交成功入口从链接改为按钮：

```vue
<button
  v-if="submittedBatchId"
  class="inline-link-button"
  type="button"
  @click="openBatchWindow(submittedBatchId)"
>
  Open batch
</button>
```

### 批次窗口骨架

在角色 drawer 和 role side panel 附近新增批次窗口，建议放在模板末尾，确保 fixed 层级清晰。

```vue
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
  <section
    v-if="isBatchWindowOpen"
    class="batch-window"
    role="dialog"
    aria-modal="true"
    aria-labelledby="batch-window-title"
  >
    <header class="batch-window-header">
      <div>
        <p class="section-label">Batch console</p>
        <h2 id="batch-window-title">Batches</h2>
      </div>
      <button class="icon-button" type="button" aria-label="Close batch console" @click="closeBatchWindow">x</button>
    </header>

    <section class="batch-window-toolbar">
      <!-- search / status / sort / refresh -->
    </section>

    <section class="batch-status-line" aria-live="polite">
      <p>{{ batchStatusText }}</p>
    </section>

    <section class="batch-window-body">
      <aside class="batch-window-list" aria-label="Task batches">
        <!-- batch cards -->
      </aside>
      <section class="batch-window-detail" aria-label="Batch detail">
        <!-- selected batch detail -->
      </section>
    </section>
  </section>
</Transition>
```

### 批次列表

批次卡片推荐使用 button 或 article + button。为避免整卡点击语义复杂，建议主按钮承载打开详情行为：

```vue
<article
  v-for="batch in batches"
  :key="batch.batch_id"
  class="batch-window-card"
  :class="{ selected: selectedBatchId === batch.batch_id }"
>
  <div class="batch-card-head">
    <div>
      <h3>{{ batch.title }}</h3>
      <p>{{ batch.batch_id }}</p>
    </div>
    <span class="status-badge" :class="`status-${batch.derived_status}`">
      {{ batch.derived_status }}
    </span>
  </div>
  <dl class="batch-card-metrics">
    <div><dt>Total</dt><dd>{{ batch.total_tasks }}</dd></div>
    <div><dt>Done</dt><dd>{{ batch.completed_count }}</dd></div>
    <div><dt>Success</dt><dd>{{ batch.success_rate }}%</dd></div>
  </dl>
  <button class="ghost-button compact-action" type="button" @click="openBatchDetail(batch.batch_id)">
    View detail
  </button>
</article>
```

### 批次详情

详情区域拆为三块：

- 批次头部摘要：标题、状态、完成数。
- 任务列表：每个任务可点击，切换右侧内容。
- 任务详情：时间线和 deliverables。

建议避免把详情再做成独立浮层。它应该在中等窗口右侧区域内展示。

## 产物渲染迁移

`app.js` 里的 artifact 渲染逻辑比较多，迁移时按 artifact 类型拆成模板分支：

- `code_file`
- `code_patch`
- `test_report`
- `document`
- `analysis_report`
- `data_file`
- fallback generic json

推荐新增函数：

```js
function artifactsForSelectedTask() {
  const allArtifacts = selectedBatchSummary.value?.artifacts || [];
  if (!selectedTaskId.value) {
    return allArtifacts;
  }
  return allArtifacts.filter((artifact) => artifact.task_id === selectedTaskId.value);
}
```

如果现有后端仍可能返回旧式 json code result，需要继续调用 `inferCodeArtifactsFromJsonArtifacts()`，保持旧页面行为一致。

## Escape 与滚动锁

调整 `handleKeydown(event)` 的优先级：

1. 如果批次窗口打开，关闭批次窗口。
2. 否则如果角色详情打开，关闭角色详情。
3. 否则如果角色编辑打开，关闭角色编辑。
4. 否则如果角色抽屉打开，关闭角色抽屉。
5. 否则如果侧边菜单打开，关闭侧边菜单。

示例：

```js
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
```

`onBeforeUnmount()` 中需要移除可能新增的 body class：

```js
document.body.classList.remove("modal-locked");
```

## 路由兼容

推荐将旧批次页面路径返回 Vue 入口文件，让前端根据 path 自动打开批次窗口。

### FastAPI 调整

在 `src/apps/api/app.py` 中：

```py
@app.get("/console/batches")
def console_batches() -> FileResponse:
    return _agent_registry_page()


@app.get("/console/batches/{batch_id}")
def console_batch_detail(batch_id: str) -> FileResponse:
    return _agent_registry_page()
```

这样旧路径仍返回 200，同时不会继续进入旧独立页面。

### 前端路径解析

在 `onMounted()` 中增加：

```js
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
```

然后在 `onMounted()` 中调用：

```js
onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
  loadRegistry();
  openInitialBatchWindowFromLocation();
});
```

注意：如果旧路径返回 Vue 入口，Vite 构建 base 是 `/console/vue/`，当前服务端已有 `/console/vue` 静态挂载，需确认返回内容中的资源路径仍可正确加载。

## 样式设计

新增样式放在 `AgentRegistry.vue` 的 `<style>` 中。

核心尺寸：

```css
.batch-window {
  position: fixed;
  left: 50%;
  top: 50%;
  z-index: 50;
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr);
  width: min(960px, calc(100vw - 40px));
  max-height: min(760px, calc(100vh - 40px));
  transform: translate(-50%, -50%);
  overflow: hidden;
  border-radius: 24px;
}
```

层级建议：

- `drawer-overlay`: 20
- `role-drawer`: 30
- `role-side-panel`: 40
- `batch-window-overlay`: 45
- `batch-window`: 50

窗口 body：

```css
.batch-window-body {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
  overflow: hidden;
}

.batch-window-list,
.batch-window-detail {
  min-height: 0;
  overflow: auto;
}
```

移动端：

```css
@media (max-width: 720px) {
  .batch-window {
    inset: 10px;
    left: auto;
    top: auto;
    width: auto;
    max-height: none;
    transform: none;
    border-radius: 18px;
  }

  .batch-window-body {
    grid-template-columns: 1fr;
  }
}
```

视觉要求：

- 复用当前黑色半透明面板、紫色边框、高亮 focus 样式。
- 按钮文案不能挤出容器。
- 卡片圆角建议不超过现有风格，不新增大面积装饰背景。
- 弹窗是中等窗口，不要做成右侧全高 drawer。

## 测试更新

### `src/tests/test_agent_registry_page.py`

新增或调整断言：

- Vue 源码包含 `openBatchWindow`。
- Vue 源码包含 `isBatchWindowOpen`。
- Vue 源码包含 `batch-window`。
- 侧边菜单中的 `Batch Console` 不再是 `href="/console/batches"`。
- 提交成功后的 `Open batch` 不再使用 `/console/batches/${submittedBatchId}`。
- 页面仍包含已有 Agent 控制台基础元素。

建议断言：

```py
assert "openBatchWindow" in component_source
assert "isBatchWindowOpen" in component_source
assert "batch-window" in component_source
assert 'href="/console/batches"' not in component_source
assert "/console/batches/${submittedBatchId}" not in component_source
```

如果旧源码仍保留 `/console/batches` 兼容解析字符串，需要避免断言过宽。可以改成检查具体模板片段不再使用 `href`。

### `src/tests/test_task_batch_list.py`

保留 API 行为测试，调整页面测试：

- `/console/batches` 返回 200。
- `/console/batches` 返回 Vue 入口或 Agent 控制台内容。
- `/console/batches/sample-batch-id` 返回 200。
- 新主实现不再依赖 `/console/assets/app.js`。

建议把旧的“独立页包含 detail panel”断言改为“兼容路径返回控制台入口”：

```py
def test_console_batches_route_returns_agent_console_entry() -> None:
    response = client.get("/console/batches")
    assert response.status_code == 200
    assert "/console/vue/" in response.text or "Agent Registry" in response.text
```

如果保留旧独立页作为兼容入口，则不要改这个测试。但主入口测试必须放到 `test_agent_registry_page.py`，确认批次窗口已迁入 Vue。

## 构建与验证命令

推荐执行：

```powershell
python -m pytest src/tests/test_agent_registry_page.py src/tests/test_task_batch_list.py
npm run build
```

如果测试依赖数据库环境，先确认 `DATABASE_URL` 已设置，并使用项目当前测试数据库。

手动验证：

1. 启动服务。
2. 打开 `/console/agents`。
3. 展开左侧菜单，点击 `Batch Console`。
4. 确认批次窗口居中打开，浏览器地址不跳转。
5. 搜索、状态筛选、排序、刷新可用。
6. 点击批次，右侧详情加载。
7. 切换任务，任务流和 deliverables 更新。
8. 提交任务后点击 `Open batch`，对应批次详情打开。
9. Escape、遮罩、关闭按钮均可关闭。
10. 访问 `/console/batches` 和 `/console/batches/sample-batch-id` 不报错。

## 实施顺序

1. 在 `AgentRegistry.vue` 新增批次窗口状态和请求方法。
2. 迁移批次列表工具函数和列表渲染模板。
3. 迁移批次详情、任务列表、时间线和产物展示。
4. 替换侧边菜单 `Batch Console` 链接为按钮。
5. 替换提交成功后的 `Open batch` 链接为按钮。
6. 增加批次窗口模板和样式。
7. 调整 Escape 和 body 滚动锁逻辑。
8. 调整 `/console/batches`、`/console/batches/{batch_id}` 服务端路由兼容。
9. 增加旧路径自动打开窗口的前端解析逻辑。
10. 更新测试。
11. 运行测试。
12. 运行前端构建。
13. 手动检查桌面和移动布局。

## 回滚策略

如果 Vue 迁移后出现严重问题：

1. 将侧边菜单 `Batch Console` 恢复为 `href="/console/batches"`。
2. 将提交成功 `Open batch` 恢复为 `/console/batches/{submittedBatchId}` 链接。
3. 将 `src/apps/api/app.py` 中 `/console/batches` 和 `/console/batches/{batch_id}` 恢复为旧 HTML 返回。
4. 保留旧 `src/apps/web/index.html`、`app.js`、`styles.css` 即可恢复原路径体验。

回滚时不需要改后端 API，因为本次方案不改 API。

## 验收标准

- `/console/agents` 中 `Batch Console` 打开的是中等窗口，不再页面跳转。
- 批次窗口内可完成列表加载、搜索、筛选、排序、刷新。
- 批次详情在同一窗口内展示，不进入独立详情页。
- 提交任务成功后的 `Open batch` 可打开新建批次详情。
- `/console/batches` 和 `/console/batches/{batch_id}` 仍返回 200。
- Escape、遮罩、关闭按钮可关闭窗口。
- 批次窗口不会和 Agent Roles 抽屉、角色详情、角色编辑面板产生不可关闭的层级冲突。
- 目标测试通过。
- `npm run build` 成功，dist 产物已更新。
