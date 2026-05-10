# Task Forge 登录界面需求文档

## 背景

当前 Task Forge 已具备任务提交、Agent 角色管理、批次控制台、任务执行轨迹和产物下载等后台控制台能力，但站点入口直接进入控制台，缺少统一登录界面。新增登录界面需要延续现有黑紫色控制台风格，并在登录前让用户明确知道登录后可进入哪些核心功能。

## 目标

- 为网站增加统一登录入口，默认访问 `/` 时展示登录界面。
- 保留 `/login` 作为显式登录页面地址。
- 登录成功后进入现有 `/console/agents` 控制台，不改动现有任务、批次、Agent 角色和产物 API 行为。
- 登录界面需要体现现有功能范围：任务提交、Agent 角色管理、批次流转和产物审查。
- 当前阶段只实现前端登录界面和基础表单校验，为后续接入真实认证 API 预留交互位置。

## 页面结构

1. 品牌与能力说明区
   - 展示 `Task Forge` 品牌标题。
   - 使用与现有 Agent Registry 一致的深色、玻璃态、紫色强调风格。
   - 展示三个能力摘要：Task Intake、Agent Roles、Batch Flow。

2. 登录表单区
   - 字段包含 Workspace、Account、Password。
   - Account 使用 email 输入类型。
   - Password 至少 6 位。
   - 提供 `Keep me signed in` 选项。
   - 提供主操作按钮 `Enter Console`。
   - 提供临时 `Continue without auth` 链接进入现有控制台，便于当前无真实认证后端时继续调试。

## 功能需求

- 访问 `/` 返回登录页面。
- 访问 `/login` 返回同一个登录页面。
- 登录页面静态资源通过 `/console/assets/login.css` 和 `/console/assets/login.js` 加载。
- 表单提交时执行前端校验：
  - Workspace 不能为空。
  - Account 不能为空且必须是有效邮箱格式。
  - Password 长度不能少于 6 个字符。
- 校验失败时在表单内展示错误提示，不刷新页面。
- 校验成功时：
  - 展示进入控制台的状态提示。
  - 根据 `Keep me signed in` 写入 `localStorage` 或 `sessionStorage`。
  - 跳转到 `/console/agents`。

## 非功能需求

- 不新增后端认证、会话、权限或数据库表。
- 不改变现有 `/console/agents`、`/console/batches`、`/task-batches`、`/agents` 等功能行为。
- 登录页需支持桌面和移动端布局。
- 表单控件需要具备明确 focus、hover 和错误状态。
- 页面文本不得遮挡输入框、按钮或能力卡片内容。
- 样式避免引入新的 UI 组件库，继续使用原生 HTML/CSS/JavaScript。

## 验收标准

- 打开 `/` 可以看到 Task Forge 登录界面，而不是直接进入控制台。
- 打开 `/login` 可以看到同一登录界面。
- 登录页展示 Workspace、Account、Password、Keep me signed in 和 Enter Console。
- 空表单提交会展示校验错误。
- 输入合法表单后会跳转到 `/console/agents`。
- `/console/agents` 仍可正常访问现有 Agent Registry 控制台。
- 页面在窄屏下切换为单列布局，登录表单和能力说明不重叠。

## 涉及文件

- `src/apps/api/app.py`
- `src/apps/web/login.html`
- `src/apps/web/login.css`
- `src/apps/web/login.js`
- `src/tests/test_agent_registry_page.py`
