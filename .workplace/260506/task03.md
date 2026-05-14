# 交付物类型识别与前端下载

## 背景

当前任务意图识别已经能输出 `deliverable_contract.expected_artifact_types`，用于区分 `document`、`code_file`、`code_patch` 等系统内部 artifact 类型。但前端缺少一个更直接面向用户的交付物类型，例如 Markdown、TXT、代码，也缺少基于该类型展示和下载的稳定入口。

本次目标是在意图识别、artifact 生成、API 返回和前端展示之间补齐一条交付物类型链路。

## 实现内容

1. 意图识别 contract 增加 `deliverable_type`
   - 新增允许值：`markdown`、`txt`、`code`、`json`。
   - 规则识别支持 Markdown、`.txt`、plain text、代码、patch/diff 等表达。
   - 模型意图请求 prompt 增加 `available_deliverable_types` 和 `deliverable_type` 输出要求。
   - 模型返回缺失 `deliverable_type` 时，会用规则识别结果补齐。

2. Artifact 生成携带交付物类型
   - artifact metadata 增加 `deliverable_type`。
   - `code_file`、`code_patch` 归类为 `code`。
   - Markdown 文档归类为 `markdown`。
   - TXT 文档归类为 `txt`。
   - JSON 主输出归类为 `json`。
   - 旧格式 `result.code` 在 intent 要求 `txt` 时，生成纯文本 `.txt`，不再包 Markdown 代码块。

3. API 返回和下载
   - `BatchArtifactRead`、`ArtifactRead` 增加顶层 `deliverable_type`。
   - `/task-batches/{batch_id}/summary` 返回每个 artifact 的交付物类型。
   - 新增 `GET /artifacts/{artifact_id}/download`，按 artifact 内容生成可下载响应。

4. 前端展示与下载
   - Batch Console 的 Deliverables 区域展示用户可读类型：`Markdown`、`TXT`、`Code`、`JSON`。
   - 每个交付物卡片增加 `Download` 按钮。
   - 前端会按类型和路径生成下载文件名，支持真实 artifact 和前端推断出的旧格式代码 artifact。

## 涉及文件

- `src/packages/core/intent.py`
- `src/apps/api/intent_recognition.py`
- `src/packages/core/artifacts.py`
- `src/packages/core/schemas.py`
- `src/apps/api/routers/artifacts.py`
- `src/apps/api/routers/task_batches.py`
- `src/apps/web/vue/src/AgentRegistry.vue`
- `src/apps/web/dist/index.html`
- `src/apps/web/dist/assets/*`
- `src/tests/test_intent_recognition.py`
- `src/tests/test_artifact_payloads.py`
- `src/tests/test_artifacts_api.py`
- `src/tests/test_task_batch_summary.py`
- `src/tests/test_task_batch_normalization_api.py`
- `src/tests/test_agent_registry_page.py`

## 验证结果

已执行：

```powershell
python -m pytest src\tests\test_intent_recognition.py src\tests\test_artifact_payloads.py
python -m pytest src\tests\test_artifacts_api.py src\tests\test_task_batch_summary.py src\tests\test_task_batch_normalization_api.py src\tests\test_agent_registry_page.py
npm run build
```

结果：

- 意图识别与 artifact payload 测试：18 passed。
- API、批次汇总、归一化、前端源码断言测试：23 passed。
- Vite build 成功，生成新的前端 dist 资源。

备注：单独执行 `py_compile` 时，当前环境因为 `src/packages/core/__pycache__` 写入权限被拒绝而失败；目标 pytest 已完成模块导入和行为验证。
