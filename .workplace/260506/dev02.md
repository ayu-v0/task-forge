# 批次管理台 View Detail 视觉优化方案

## 问题判断

当前批次窗口中的 `View detail` 存在几个明显问题：

- 它是卡片底部的普通 `ghost-button`，视觉重量低，像临时追加的操作。
- 卡片本身 hover 和 selected 状态已经存在，但真正的详情入口只在底部按钮上，交互焦点割裂。
- `View detail` 文案占空间，却没有提供更多信息；用户已经在批次卡片里，动作本质是“选择这条批次”。
- 左侧列表是高频扫描区，重复出现的文字按钮会制造噪音。
- 当前按钮没有方向感或打开状态反馈，和右侧详情区域的联动关系不够明确。

本次优化目标不是重做批次详情能力，而是把“查看详情”入口做成更自然、更精致的批次卡片选择体验。

## 目标

1. 移除生硬的 `View detail` 文本按钮观感。
2. 让批次卡片整体呈现为可选择的对象。
3. 强化当前选中批次与右侧详情面板的视觉关联。
4. 保持键盘可访问性和屏幕阅读器语义。
5. 不修改后端 API、不修改批次详情数据流。
6. 控制改动范围，只调整 `AgentRegistry.vue` 中批次列表卡片模板和样式。

## 非目标

- 不改 `/task-batches`、`/task-batches/{batchId}/summary`、`/tasks/{taskId}/timeline` 接口。
- 不重做批次详情右侧区域。
- 不引入新的前端依赖或图标库。
- 不重构整个 `AgentRegistry.vue`。
- 不调整 Agent Roles 相关 UI。

## 设计方向

将当前：

```vue
<article class="batch-window-card">
  ...
  <button class="ghost-button compact-action">View detail</button>
</article>
```

改为“可选择批次卡片”：

```vue
<button
  class="batch-window-card batch-window-card-button"
  type="button"
  :class="{ selected: selectedBatchId === batch.batch_id }"
  :aria-pressed="selectedBatchId === batch.batch_id"
  @click="openBatchDetail(batch.batch_id)"
>
  ...
  <span class="batch-card-open-indicator" aria-hidden="true">›</span>
</button>
```

说明：

- 整张卡片变成一个按钮，用户点击卡片任意位置即可打开详情。
- `View detail` 文本不再出现在每张卡片底部。
- 使用右侧的轻量箭头作为方向提示，表达“打开右侧详情”。
- `aria-pressed` 表示当前卡片是否被选中。
- 卡片内不再使用 `dl`，改成普通 `div/span` 指标块，避免在 `button` 中嵌套不合适的结构。

## 交互方案

### 默认态

- 卡片保持低噪音深色面板。
- 标题、状态、关键指标清晰可扫。
- 右侧显示一个小型圆形箭头区域，作为详情入口提示。

### Hover 态

- 卡片边框变亮。
- 背景出现轻微紫色高亮。
- 箭头区域从低亮变为高亮。
- 卡片轻微上移，但移动幅度不超过 `1px`。

### Focus 态

- 使用明确的 focus ring。
- 不依赖 hover 才能看出可操作。
- 键盘 Tab 聚焦到卡片时，和 hover 态具有一致的可见反馈。

### Selected 态

- 左侧增加一条细的紫色强调线。
- 卡片背景比 hover 态更稳定、更明确。
- 箭头区域保持高亮，表示右侧详情正在展示这条批次。
- 不改变卡片尺寸，避免列表跳动。

### Loading 态

当点击批次后，右侧详情加载期间：

- 当前卡片立即进入 selected 态。
- 右侧保留现有 `Loading batch detail...`。
- 不需要在卡片内增加 spinner，避免列表视觉过重。

## 模板改造

### 当前结构

位置：`src/apps/web/vue/src/AgentRegistry.vue`

当前批次卡片区域大致为：

```vue
<article
  v-for="batch in batches"
  :key="batch.batch_id"
  class="batch-window-card"
  :class="{ selected: selectedBatchId === batch.batch_id }"
>
  <div class="batch-card-head">...</div>
  <dl class="batch-card-metrics">...</dl>
  <p class="batch-card-time">Updated {{ formatDate(batch.updated_at) }}</p>
  <button class="ghost-button compact-action" type="button" @click="openBatchDetail(batch.batch_id)">
    View detail
  </button>
</article>
```

### 建议替换结构

```vue
<button
  v-for="batch in batches"
  :key="batch.batch_id"
  class="batch-window-card batch-window-card-button"
  :class="{ selected: selectedBatchId === batch.batch_id }"
  type="button"
  :aria-pressed="selectedBatchId === batch.batch_id"
  @click="openBatchDetail(batch.batch_id)"
>
  <span class="batch-card-accent" aria-hidden="true"></span>

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
    <span class="batch-card-open-indicator" aria-hidden="true">›</span>
  </span>
</button>
```

注意：

- 这里用 `span/small/strong` 组成按钮内部内容，避免在 button 里嵌套 `article`、`dl`、`button`。
- 如果团队不希望整卡是 button，也可以保留 `article`，把底部按钮改为右上角 icon button；但整卡 button 的体验更直接。
- `›` 是视觉提示，屏幕阅读器不读它；按钮本身通过标题和 `aria-pressed` 提供语义。

## 样式改造

### 新增/替换 class

建议保留 `.batch-window-card` 作为基础样式，并新增：

- `.batch-window-card-button`
- `.batch-card-accent`
- `.batch-card-title-block`
- `.batch-card-title`
- `.batch-card-id`
- `.batch-card-footer`
- `.batch-card-open-indicator`

### 样式建议

```css
.batch-window-card-button {
  position: relative;
  display: grid;
  gap: 12px;
  width: 100%;
  min-height: 156px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.025)),
    rgba(255, 255, 255, 0.035);
  color: #f8fafc;
  text-align: left;
  padding: 14px 14px 12px 16px;
  overflow: hidden;
  transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease, transform 160ms ease;
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
.batch-card-footer {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
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
.batch-card-metrics small {
  color: #94a3b8;
  font-size: 0.76rem;
  line-height: 1.35;
}

.batch-card-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
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
```

## 响应式要求

移动端或窄窗口下：

- 卡片仍保持整卡可点击。
- 指标区从 3 列改为 1 列或保留 3 列但缩小 padding，优先避免文字挤压。
- 箭头仍在 footer 右侧。
- 卡片高度可以自然增长，但 hover/selected 不应导致布局跳动。

建议补充：

```css
@media (max-width: 720px) {
  .batch-card-metrics {
    grid-template-columns: 1fr;
  }
}
```

## 测试调整

更新 `src/tests/test_agent_registry_page.py` 或 `src/tests/test_task_batch_list.py` 中的源码断言。

建议新增断言：

```py
assert "batch-window-card-button" in component_source
assert "batch-card-open-indicator" in component_source
assert "aria-pressed" in component_source
assert "View detail" not in component_source
```

如果仍保留旧独立页面 `src/apps/web/app.js`，不要全局搜索断言 `View detail` 不存在。只针对 `AgentRegistry.vue` 源码断言即可，因为旧文件里可能仍有 `View detail`。

## 验证计划

执行：

```powershell
npm run build
python -m pytest src/tests/test_agent_registry_page.py src/tests/test_task_batch_list.py
```

手动验证：

1. 打开 `/console/agents`。
2. 打开 `Batch Console` 中等窗口。
3. 确认左侧批次列表不再出现普通 `View detail` 按钮。
4. 点击任意批次卡片，右侧详情加载。
5. 选中卡片有明显 selected 状态。
6. Tab 聚焦到卡片时，有清晰 focus 样式。
7. Enter/Space 可打开详情。
8. 窄窗口下卡片内容不重叠、不挤出容器。

## 实施顺序

1. 修改 `AgentRegistry.vue` 中批次列表卡片模板。
2. 将卡片根元素从 `article` 调整为 `button`。
3. 删除卡片底部 `View detail` 文本按钮。
4. 增加卡片 footer 和箭头指示元素。
5. 替换 `.batch-window-card` 相关 CSS，新增按钮态、focus 态、selected 态。
6. 保留批次详情加载函数 `openBatchDetail(batch.batch_id)` 不变。
7. 更新测试源码断言。
8. 运行构建和目标测试。

## 风险与处理

- 风险：`button` 中原有 `dl` 结构不适合保留。
  - 处理：指标区改为 `span/small/strong` 结构。

- 风险：整卡变成 button 后，内部不能再嵌套其他 button。
  - 处理：完全移除内部 `View detail` 按钮。

- 风险：选中态和 hover 态太接近，用户看不出当前详情来源。
  - 处理：selected 态增加左侧内嵌高亮线和稳定背景。

- 风险：卡片高度变化导致列表跳动。
  - 处理：设置合理 `min-height`，hover/selected 不改变 padding 和 border 宽度。

## 验收标准

- 批次卡片中不再显示生硬的 `View detail` 文本按钮。
- 整张批次卡片可以点击并打开右侧详情。
- 当前选中批次有明确、稳定、美观的 selected 状态。
- 键盘用户可以通过 Tab 聚焦卡片，并通过 Enter/Space 打开详情。
- 批次卡片在桌面和移动宽度下无文字溢出、重叠或布局跳动。
- 不改变批次详情加载、任务切换、产物展示等已有行为。
- `npm run build` 和目标测试通过。
