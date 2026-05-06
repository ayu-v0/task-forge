# 将 /console/batches 改为中等窗口的完整方案

## 背景

当前批次控制台有两层“页面化”行为：

- `/console/batches` 由 `src/apps/api/app.py` 返回 `src/apps/web/index.html`，是一个独立 HTML 页面。
- Agent 控制台 `src/apps/web/vue/src/AgentRegistry.vue` 中的侧边菜单 `Batch Console` 和提交成功后的 `Open batch` 都通过链接跳转到批次相关页面。
- 批次列表页内部已经把单个批次详情做成了右侧浮层，但批次列表本身仍然是独立页面。

本次目标是把“批次控制台列表”从独立页面改为控制台内的中等窗口，让用户在 Agent 控制台里打开、筛选、查看批次，不离开当前主界面。

## 目标

1. 在 `/console/agents` 当前 Vue 控制台中新增一个中等尺寸弹窗，用于承载 Batch Console。
2. 侧边菜单中的 `Batch Console` 不再跳转页面，改为打开弹窗。
3. 任务提交成功后的 `Open batch` 不再跳转 `/console/batches/{batch_id}`，改为打开批次弹窗并自动定位/加载对应批次详情。
4. 弹窗内保留现有批次列表能力：搜索、状态筛选、排序、刷新、批次卡片指标。
5. 弹窗内继续支持查看批次详情，但详情不再使用独立页面跳转。
6. 保留旧 URL 的兼容处理，避免已有书签、测试或外部引用立刻失效。

## 非目标

- 不修改 `task-batches`、`tasks`、`artifacts` 等后端 API 的数据结构。
- 不重做 Agent Registry 主页面信息架构。
- 不删除批次详情 API 或运行详情页 `/console/runs/{run_id}`。
- 不手工修改压缩后的 `dist/assets/*.js`，构建产物应通过 `npm run build` 生成。

## 现状文件

- `src/apps/api/app.py`
  - `/console/batches` 返回 `src/apps/web/index.html`。
  - `/console/batches/{batch_id}` 返回 `src/apps/web/batch-detail.html`。
- `src/apps/web/index.html`
  - 批次列表独立页 DOM。
  - 包含 `batch-detail-overlay`、`batch-detail-panel` 作为批次详情浮层。
- `src/apps/web/app.js`
  - 批次列表加载、筛选、排序、刷新。
  - `openBatchDetail(batchId)` 会在页面内打开批次详情面板。
  - 已经通过 `/task-batches/${batchId}/summary`、`/tasks/${task.task_id}/timeline` 加载详情数据。
- `src/apps/web/styles.css`
  - 批次独立页样式和详情面板样式。
- `src/apps/web/vue/src/AgentRegistry.vue`
  - 当前主控制台 Vue 组件。
  - 侧边菜单中有 `<a href="/console/batches">Batch Console</a>`。
  - 提交成功后有 `<a :href="\`/console/batches/${submittedBatchId}\`">Open batch</a>`。
- `src/tests/test_task_batch_list.py`
  - 覆盖 `/console/batches` 可访问、批次页内嵌详情面板、前端资产不再跳详情页等断言。
- `src/tests/test_agent_registry_page.py`
  - 覆盖 Agent 控制台页面、侧边菜单和 Vue 源码结构。

## 交互设计

### 入口

1. Agent 控制台侧边菜单：
   - 将 `Batch Console` 从 `<a>` 改成 `<button>`。
   - 点击后调用 `openBatchWindow()`。
   - 打开后自动关闭侧边菜单，避免菜单和弹窗重叠。

2. 任务提交成功后：
   - 将 `Open batch` 从链接改成按钮。
   - 点击后调用 `openBatchWindow(submittedBatchId)`。
   - 弹窗打开后加载批次列表，并同时加载该 batch 的详情。

3. 旧 URL：
   - `/console/batches` 保留可访问，但建议返回 Agent 控制台，并通过 query/hash 打开批次弹窗，例如 `/console/agents?window=batches`。
   - `/console/batches/{batch_id}` 保留可访问，但建议返回 Agent 控制台，并通过 query/hash 打开对应批次，例如 `/console/agents?window=batches&batch_id=...`。
   - 如果暂时不做服务端重定向，也可以继续返回旧页面作为兼容入口，但新主路径必须是 Agent 控制台中的弹窗。

### 窗口形态

中等窗口建议使用居中 modal，而不是右侧全高 drawer：

- 宽度：`width: min(960px, calc(100vw - 40px))`
- 高度：`max-height: min(760px, calc(100vh - 40px))`
- 布局：header、toolbar、status、batch list/detail body。
- 桌面端居中显示，带遮罩。
- 移动端降级为接近全屏，但仍保留 modal 语义和关闭按钮。

窗口关闭方式：

- 点击关闭按钮。
- 点击遮罩。
- 按 Escape。

可访问性要求：

- 弹窗容器使用 `role="dialog"` 和 `aria-modal="true"`。
- 标题绑定 `aria-labelledby`。
- 打开弹窗时把主页面滚动锁定。
- 关闭后恢复页面滚动。
- 至少保证按钮可 Tab 聚焦，Escape 可关闭。

## 数据与状态设计

在 `AgentRegistry.vue` 中新增批次窗口相关状态：

- `isBatchWindowOpen`
- `batchSearch`
- `batchStatusFilter`
- `batchSort`
- `batchStatusText`
- `batchLoading`
- `batchError`
- `batches`
- `selectedBatchId`
- `selectedBatchSummary`
- `selectedTaskId`
- `selectedTaskTimelineItems`
- `batchAbortController`
- `batchDetailAbortController`
- `taskTimelineAbortController`

核心方法：

- `openBatchWindow(batchId = "")`
  - 打开弹窗。
  - 加载批次列表。
  - 如果传入 `batchId`，同时调用 `openBatchDetail(batchId)`。

- `closeBatchWindow()`
  - 关闭弹窗。
  - 中止正在进行的批次列表、批次详情、任务时间线请求。
  - 清理选中状态，但可以保留筛选条件，方便用户下次继续。

- `loadBatches()`
  - 使用现有 `/task-batches` 查询参数：`search`、`status`、`sort`。
  - 更新 `batches` 和 `batchStatusText`。

- `openBatchDetail(batchId)`
  - 使用现有 `/task-batches/{batchId}/summary`。
  - 成功后默认选择第一条 task。

- `selectBatchTask(taskId)`
  - 切换任务。
  - 使用现有 `/tasks/{taskId}/timeline` 加载任务流。

## UI 结构方案

在 `AgentRegistry.vue` 模板中新增：

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

    <section class="batch-window-toolbar">...</section>
    <section class="batch-window-body">...</section>
  </section>
</Transition>
```

窗口 body 推荐使用两列：

- 左侧：批次列表，宽度约 `320px` 到 `380px`。
- 右侧：选中批次详情，剩余空间。
- 当没有选中批次时，右侧显示空状态。
- 小屏幕下改为单列，先列表后详情。

## 代码迁移策略

### 推荐做法：把批次控制台逻辑迁入 Vue

将 `src/apps/web/app.js` 的批次列表与详情逻辑按 Vue 方式迁入 `AgentRegistry.vue`：

- 把 DOM 查询改成 `ref` 状态和模板渲染。
- 把 `renderBatches`、`renderBatchDetail`、`renderTaskTimeline`、`renderArtifacts` 拆成 Vue 模板或小型渲染函数。
- 继续复用现有 API 和字段，不改后端。
- CSS 迁移到 `AgentRegistry.vue` 的 `<style>` 内，与当前控制台样式变量保持一致。

优点：

- 不需要在 Vue 页面中嵌入独立 HTML 或 iframe。
- 状态、弹窗、Escape 关闭逻辑可以与现有 drawer/detail panel 统一管理。
- 后续维护只需要看一个主控制台组件。

### 备选做法：临时 iframe

可以把旧 `/console/batches` 放进 modal iframe，但不建议作为最终方案：

- iframe 内还有自己的页面 shell 和详情浮层，视觉和滚动体验会比较割裂。
- 无法方便支持提交成功后直接打开指定 batch 详情。
- 测试和可访问性更难稳定。

因此本方案选择“迁入 Vue”的实现。

## 路由兼容方案

短期兼容建议：

1. `/console/batches`
   - 返回 Agent 控制台入口。
   - 前端读取 `window=batches` 后自动打开批次窗口。

2. `/console/batches/{batch_id}`
   - 返回 Agent 控制台入口。
   - 前端从路径或 query 中解析 `batch_id`，自动打开批次窗口并加载详情。

3. `/console/assets/app.js`、`src/apps/web/index.html`
   - 可以暂时保留，降低回滚成本。
   - 新测试不再依赖这些资产作为主实现。

如果必须完全避免 URL 内容变化，可以在 FastAPI 中继续让这两个路径返回同一个 Vue 入口文件，由 Vue 根据 `location.pathname` 判断是否打开批次窗口。

## 实施步骤

1. 修改 `src/apps/web/vue/src/AgentRegistry.vue`
   - 新增批次窗口状态、请求方法、渲染模板、关闭逻辑。
   - 侧边菜单 `Batch Console` 改为按钮。
   - 提交成功后的 `Open batch` 改为按钮。
   - `handleKeydown` 增加批次窗口关闭优先级。

2. 迁移批次列表能力
   - 从 `src/apps/web/app.js` 迁移 `formatDate`、`escapeHtml` 等必要工具函数。
   - 迁移列表查询、筛选、排序和刷新逻辑。
   - 用 Vue 模板渲染批次卡片。

3. 迁移批次详情能力
   - 迁移 `/task-batches/{batchId}/summary` 加载逻辑。
   - 迁移任务列表、任务时间线、deliverables 渲染。
   - 保留 code file、patch、test report、generic artifact 的展示能力。

4. 增加弹窗样式
   - 新增 `.batch-window-overlay`、`.batch-window`、`.batch-window-toolbar`、`.batch-window-body` 等样式。
   - 桌面端中等窗口居中。
   - 小屏幕改为接近全屏。
   - 避免复用全高 drawer 的视觉尺寸。

5. 调整 FastAPI 页面路由
   - 将 `/console/batches` 和 `/console/batches/{batch_id}` 指向 Vue 控制台入口，或者保留旧返回但新入口不再使用。
   - 推荐添加测试确认旧路径不会 404，并能返回 Vue 入口。

6. 更新测试
   - `src/tests/test_agent_registry_page.py`
     - 断言 Vue 源码包含 `openBatchWindow`。
     - 断言 `Batch Console` 是按钮行为，而不是 `href="/console/batches"`。
     - 断言提交成功入口不再使用 `/console/batches/${submittedBatchId}` 跳转。
   - `src/tests/test_task_batch_list.py`
     - 调整 `/console/batches` 的页面断言：从旧 `Batch Console` 独立页改为 Vue 入口兼容断言。
     - 保留 API 列表测试。
     - 如果保留旧页面，则新增测试应明确“旧页面可访问但不是主入口”。

7. 构建与验证
   - 运行 Python 页面/接口测试：
     - `python -m pytest src/tests/test_agent_registry_page.py src/tests/test_task_batch_list.py`
   - 构建 Vue 产物：
     - `npm run build`
   - 手工验证：
     - 打开 `/console/agents`。
     - 从侧边菜单打开批次弹窗。
     - 搜索、筛选、排序、刷新可用。
     - 点击批次卡片后右侧详情加载。
     - 提交任务后点击 `Open batch`，弹窗打开并显示对应批次详情。
     - Escape、遮罩、关闭按钮均可关闭窗口。
     - 访问 `/console/batches` 和 `/console/batches/{batch_id}` 不报错。

## 验收标准

- `/console/agents` 中点击侧边菜单 `Batch Console` 后，在当前页面打开中等尺寸弹窗。
- 打开批次弹窗不会跳转浏览器地址，也不会离开 Agent 控制台。
- 弹窗宽度在桌面端明显小于全屏，视觉上是中等窗口。
- 弹窗内可以加载批次列表，并支持搜索、状态筛选、排序和刷新。
- 点击批次后，弹窗内显示批次详情、任务列表、任务流和 deliverables。
- 任务提交成功后的 `Open batch` 可以打开同一个批次弹窗并展示新建 batch。
- Escape、遮罩、关闭按钮都能关闭弹窗，关闭后页面可正常滚动。
- `/console/batches` 和 `/console/batches/{batch_id}` 保持兼容，不出现 404。
- 相关测试和 `npm run build` 通过。

## 风险与处理

- 迁移 `app.js` 到 Vue 可能导致 artifact 展示遗漏。
  - 处理：先按现有渲染分支逐项迁移，并用源码断言覆盖 `renderCodeFileArtifact`、`renderCodePatchArtifact`、`renderTestReportArtifact` 等等价能力。

- 批次窗口、角色 drawer、角色详情面板可能同时打开。
  - 处理：打开批次窗口时关闭侧边菜单；是否关闭角色 drawer/detail 建议统一关闭，避免多个浮层竞争 Escape 和滚动锁。

- 旧测试仍假设 `/console/batches` 是独立页。
  - 处理：按新产品行为更新断言，保留旧路径可访问性测试。

- 构建产物变更较大。
  - 处理：只编辑 Vue 源码，通过 `npm run build` 生成 dist，不手工修改压缩文件。

## 建议最终文件变更

- `src/apps/web/vue/src/AgentRegistry.vue`
- `src/apps/api/app.py`
- `src/tests/test_agent_registry_page.py`
- `src/tests/test_task_batch_list.py`
- `src/apps/web/dist/index.html`
- `src/apps/web/dist/assets/*`

旧版文件 `src/apps/web/index.html`、`src/apps/web/app.js`、`src/apps/web/styles.css` 可暂时保留，等确认没有外部依赖后再做清理。
