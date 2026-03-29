# dev01 开发日志

## 任务
按照 [dev01.md](L:\Project\Python\task-forge\.workplace\260329\dev01.md) 搭建 FastAPI 服务骨架，并完成最小验收。

## 实现内容

### 1. 依赖更新
更新了 [requirements.txt](L:\Project\Python\task-forge\requirements.txt)，新增：
- `fastapi`
- `uvicorn[standard]`
- `pydantic-settings`

### 2. API 应用骨架
新增文件：
- [src/apps/api/__init__.py](L:\Project\Python\task-forge\src\apps\api\__init__.py)
- [src/apps/api/app.py](L:\Project\Python\task-forge\src\apps\api\app.py)
- [src/apps/api/settings.py](L:\Project\Python\task-forge\src\apps\api\settings.py)
- [src/apps/api/deps.py](L:\Project\Python\task-forge\src\apps\api\deps.py)

实现结果：
- 初始化了 `FastAPI` 应用
- 使用 `settings` 提供 `app_name`、`app_version`、`debug`、`database_url`
- 使用 `deps.get_db()` 提供 SQLAlchemy `Session`

### 3. Router 拆分
新增文件：
- [src/apps/api/routers/__init__.py](L:\Project\Python\task-forge\src\apps\api\routers\__init__.py)
- [src/apps/api/routers/health.py](L:\Project\Python\task-forge\src\apps\api\routers\health.py)
- [src/apps/api/routers/task_batches.py](L:\Project\Python\task-forge\src\apps\api\routers\task_batches.py)
- [src/apps/api/routers/tasks.py](L:\Project\Python\task-forge\src\apps\api\routers\tasks.py)
- [src/apps/api/routers/agents.py](L:\Project\Python\task-forge\src\apps\api\routers\agents.py)
- [src/apps/api/routers/runs.py](L:\Project\Python\task-forge\src\apps\api\routers\runs.py)
- [src/apps/api/routers/reviews.py](L:\Project\Python\task-forge\src\apps\api\routers\reviews.py)

实现结果：
- `GET /health`
- `POST /task-batches`
- `GET /task-batches/{batch_id}`
- 其余路由按文档要求先完成模块拆分和占位

### 4. Schema 修正
修改了 [src/packages/core/schemas.py](L:\Project\Python\task-forge\src\packages\core\schemas.py)

原因：
- `TaskBatchORM` 使用属性名 `metadata_json`
- `TaskBatchRead` 直接从 ORM 读取时，Pydantic 误取到了 SQLAlchemy 基类上的 `metadata`

修正：
- 为 `TaskBatchRead.metadata` 增加 `validation_alias=AliasChoices("metadata_json", "metadata")`

结果：
- `POST /task-batches` 可以正确返回响应模型

## 遇到的问题

### 1. 后台启动 uvicorn 时接口不可访问
原因：
- 后台进程启动时未继承 `DATABASE_URL`

处理：
- 在同一条 PowerShell 命令中设置环境变量、启动 `uvicorn`、执行 HTTP 验证、再停止进程

### 2. `/docs` 验证失败
原因：
- Windows PowerShell 的 `Invoke-WebRequest` 依赖 IE 引擎

处理：
- 改为使用 `-UseBasicParsing`

### 3. `POST /task-batches` 返回 500
原因：
- `metadata` 字段映射错误

处理：
- 修正 `TaskBatchRead` 的字段别名

## 验证结果
实际执行了 API 验证，结果如下：

- `GET /health`
  - 返回：`{"status":"ok"}`
- `GET /docs`
  - 返回状态码：`200`
- `POST /task-batches`
  - 返回：
```json
{"title":"demo batch","description":"first batch","created_by":"ayu","metadata":{},"id":"batch_b95b3d2963e648a0958e6305915ea2b9","created_at":"2026-03-29T21:25:26.673434+08:00","status":"draft","total_tasks":0}
```

## 结论
`task01` 已完成，满足 [dev01.md](L:\Project\Python\task-forge\.workplace\260329\dev01.md) 中的完成定义：
- FastAPI 服务可启动
- `/docs` 可访问
- `/health` 正常返回
- `POST /task-batches` 可写入 PostgreSQL
