# 交付物类型识别与下载开发文档

## 开发目标

在现有意图识别、任务执行 artifact 持久化、批次详情和前端 Deliverables 展示链路中加入面向用户的交付物类型识别。

交付物类型用于描述最终产物的用户可见格式，而不是替代系统内部 artifact 类型：

- `markdown`：Markdown 文档，通常对应 `.md`、`text/markdown`。
- `txt`：纯文本文件，通常对应 `.txt`、`text/plain`。
- `code`：代码文件或代码 patch，通常对应 `code_file`、`code_patch`。
- `json`：结构化 JSON 输出，通常对应 primary json artifact。

最终效果：

1. 意图识别能输出 `deliverable_contract.deliverable_type`。
2. Worker 生成 artifact 时将交付物类型写入 metadata、summary 或 structured output。
3. API 在 artifact 读模型中暴露顶层 `deliverable_type`。
4. Batch Console 的 Deliverables 区域展示交付物类型，并支持点击下载。

## 数据契约

### Intent Contract

在 `DeliverableContract` 中新增字段：

```py
class DeliverableContract(IntentModel):
    expected_artifact_types: list[str]
    deliverable_type: str | None
    presentation_format: str | None
    file_extension: str | None
    include_code_block: bool
    require_file_level_artifact: bool
    allow_primary_json_only: bool
```

允许值：

```py
ALLOWED_DELIVERABLE_TYPES = {
    "markdown",
    "txt",
    "code",
    "json",
}
```

归一化别名：

- `md`、`mark_down` -> `markdown`
- `text`、`plain`、`plain_text` -> `txt`
- `source`、`source_code` -> `code`

### Artifact API

`ArtifactRead` 和 `BatchArtifactRead` 增加：

```py
deliverable_type: str | None = None
```

返回示例：

```json
{
  "artifact_id": "artifact_xxx",
  "artifact_type": "document",
  "deliverable_type": "markdown",
  "content_type": "text/markdown",
  "uri": "workspace://generated/task_1.md"
}
```

### Artifact Metadata

artifact 持久化时，在 `metadata_json` 中写入：

```json
{
  "artifact_role": "final_deliverable",
  "deliverable_type": "code"
}
```

文档类 artifact 同时可在 `summary` 和 `structured_output` 中写入 `deliverable_type`，方便前端在兼容旧数据时 fallback。

## 后端实现

### 1. 意图识别规则

文件：`src/packages/core/intent.py`

规则优先级：

1. 用户要求 `patch` 或 `diff`：`expected_artifact_types=["code_patch"]`，`deliverable_type="code"`。
2. 用户要求 `markdown`、`md`：`expected_artifact_types=["document"]`，`deliverable_type="markdown"`。
3. 用户要求 `.txt`、`plain text`、`纯文本`、`文本文件`：`expected_artifact_types=["document"]`，`deliverable_type="txt"`。
4. 编码任务：`expected_artifact_types=["code_file"]`，`deliverable_type="code"`。
5. 写作、规划、调研类任务：默认 `deliverable_type="markdown"`。
6. 普通结构化任务：默认 `deliverable_type="json"`。

`presentation_format` 派生规则：

- `markdown` -> `presentation_format="markdown"`，默认扩展名 `.md`。
- `txt` -> `presentation_format="plain_text"`，默认扩展名 `.txt`。
- `code` -> 不设置 presentation format，交由语言和 artifact 类型决定。
- `json` -> 不设置 presentation format。

### 2. 模型意图识别 Prompt

文件：`src/apps/api/intent_recognition.py`

模型请求 payload 增加：

```json
"available_deliverable_types": ["code", "json", "markdown", "txt"]
```

要求模型在 `deliverable_contract` 中返回：

```json
"deliverable_type": "markdown|txt|code|json or null"
```

如果模型返回中缺失 `deliverable_type`，`normalize_model_intent_payload()` 使用规则识别结果补齐，不覆盖模型已经识别出的 `expected_artifact_types`。

### 3. Artifact 类型推导

文件：`src/packages/core/artifacts.py`

新增推导函数：

- `_normalize_deliverable_type(value)`
- `_deliverable_type_from_contract(input_snapshot)`
- `_deliverable_type_for(...)`

推导顺序：

1. deliverable item 显式 `deliverable_type`。
2. input snapshot 中的 `deliverable_contract.deliverable_type`。
3. artifact 类型推断：
   - `code_file`、`code_patch` -> `code`
   - `json` -> `json`
   - `.md` 或 markdown language -> `markdown`
   - `.txt` -> `txt`
   - `document`、`analysis_report` -> `markdown`
   - fallback -> `txt`

### 4. 旧格式输出兼容

旧 agent 可能只返回：

```json
{
  "result": {
    "code": "...",
    "language": "python"
  }
}
```

兼容策略：

- contract 要求 `document + markdown` 时，生成 Markdown 文档，并用代码块包裹代码。
- contract 要求 `document + txt` 时，生成纯文本 `.txt`，不包 Markdown 代码块。
- contract 要求 `code_file` 时，继续生成 `code_file` artifact。
- contract 要求 `code_patch` 时，不伪造 patch。

### 5. 下载接口

文件：`src/apps/api/routers/artifacts.py`

新增：

```http
GET /artifacts/{artifact_id}/download
```

响应行为：

- `code_file` 下载 `raw_content.content`。
- `code_patch` 下载 `raw_content.diff`。
- `test_report` 下载 `raw_content.output`。
- `document`、`analysis_report`、`data_file` 下载 `raw_content.content` 或 `raw_content.body`。
- 其他类型下载格式化后的 JSON。

文件名规则：

1. 优先使用 `raw_content.path` 的 basename。
2. 无 path 时按 `deliverable_type` 推导扩展名：
   - `markdown` -> `.md`
   - `txt` -> `.txt`
   - `code` -> `.txt`
   - `json` -> `.json`
   - `code_patch` -> `.diff`

## 前端实现

文件：`src/apps/web/vue/src/AgentRegistry.vue`

### 展示类型

新增函数：

- `artifactDeliverableType(artifact)`
- `artifactDeliverableLabel(artifact)`

展示优先级：

1. `artifact.deliverable_type`
2. `artifact.metadata.deliverable_type`
3. `artifact.structured_output.deliverable_type`
4. `artifact.summary.deliverable_type`
5. 按 `artifact_type` 和 `content_type` fallback

显示文案：

- `markdown` -> `Markdown`
- `txt` -> `TXT`
- `code` -> `Code`
- `json` -> `JSON`

### 下载行为

新增函数：

- `artifactDownloadContent(artifact)`
- `artifactDownloadFilename(artifact)`
- `downloadArtifact(artifact)`

前端下载使用 Blob 生成本地下载：

```js
const blob = new Blob([content], { type: artifact.content_type || "text/plain" });
const url = URL.createObjectURL(blob);
const link = document.createElement("a");
link.href = url;
link.download = artifactDownloadFilename(artifact);
link.click();
URL.revokeObjectURL(url);
```

下载内容优先级：

- `code_file` -> `raw_content.content`
- `code_patch` -> `raw_content.diff`
- `test_report` -> `raw_content.output`
- 文档类 artifact -> `raw_content.content` 或 `raw_content.body`
- fallback -> 完整 raw JSON

### UI 调整

Deliverables 卡片头部展示交付物类型，例如 `Markdown`、`TXT`、`Code`。

卡片操作区展示：

- MIME 类型或 artifact 类型。
- `Download` 按钮。

样式新增：

- `.batch-artifact-actions`
- `.artifact-download-button`

## 测试覆盖

### 意图识别

文件：`src/tests/test_intent_recognition.py`

覆盖：

- Markdown 代码任务仍识别为 coding，但交付物类型为 `markdown`。
- 写作任务交付物类型为 `markdown`。
- `.txt` / plain text 需求交付物类型为 `txt`。
- 模型意图识别返回缺失类型时能 fallback。

### Artifact Payload

文件：`src/tests/test_artifact_payloads.py`

覆盖：

- `code_file` metadata 中写入 `deliverable_type="code"`。
- Markdown document contract 生成 `.md` 和 `text/markdown`。
- TXT document contract 生成 `.txt` 和 `text/plain`，内容不包 Markdown 代码块。

### API

文件：

- `src/tests/test_artifacts_api.py`
- `src/tests/test_task_batch_summary.py`
- `src/tests/test_task_batch_normalization_api.py`

覆盖：

- artifact detail 返回顶层 `deliverable_type`。
- `/artifacts/{artifact_id}/download` 返回可下载内容和文件名。
- batch summary 返回每个 artifact 的 `deliverable_type`。
- task batch normalization 返回 recognized intent 中的 `deliverable_type`。

### 前端源码断言

文件：`src/tests/test_agent_registry_page.py`

覆盖：

- `artifactDeliverableType`
- `artifactDeliverableLabel`
- `downloadArtifact`
- `.artifact-download-button`
- `Download` 按钮文案

## 构建产物

修改 Vue 源码后需要执行：

```powershell
npm run build
```

构建会更新：

- `src/apps/web/dist/index.html`
- `src/apps/web/dist/assets/*.css`
- `src/apps/web/dist/assets/*.js`

不要手动编辑压缩后的 dist asset，应始终通过 Vite 构建生成。

## 验证命令

推荐执行：

```powershell
python -m pytest src\tests\test_intent_recognition.py src\tests\test_artifact_payloads.py
python -m pytest src\tests\test_artifacts_api.py src\tests\test_task_batch_summary.py src\tests\test_task_batch_normalization_api.py src\tests\test_agent_registry_page.py
npm run build
```

已知环境备注：

- 当前环境单独执行 `py_compile` 可能因 `src/packages/core/__pycache__` 写入权限被拒绝失败。
- pytest 已能完成模块导入和行为验证。
- pytest cache 也可能因为 `.pytest_cache` 权限产生 warning，不影响测试结果判断。

## 验收标准

- 自动任务归一化结果中包含 `input_payload.deliverable_contract.deliverable_type`。
- Markdown、TXT、代码、JSON 四类交付物能被稳定识别或 fallback 推导。
- batch summary 和 artifact detail API 返回顶层 `deliverable_type`。
- Deliverables 卡片展示用户可读的交付物类型。
- Deliverables 卡片可点击 `Download` 下载内容。
- 下载文件名优先使用 artifact path basename，缺失 path 时按类型生成合理扩展名。
- TXT contract 下的旧格式代码输出生成纯文本 `.txt`，不包 Markdown 代码块。
- 相关 pytest 和 `npm run build` 通过。

## 回滚策略

如果前端下载或类型展示出现问题：

1. 可先保留后端 `deliverable_type` 字段，仅隐藏前端 `Download` 按钮。
2. 若 API 兼容性出现问题，可保持 `metadata.deliverable_type`，暂时移除顶层 read model 字段。
3. 若意图识别误判明显，可保留字段但收窄规则，仅在用户明确提到 `.md`、`.txt`、patch、file 时设置。
4. dist 产物通过重新执行 `npm run build` 恢复，不手工修改压缩文件。
