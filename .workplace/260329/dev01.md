# task01 开发文档

## 任务目标
搭建 TaskForge 的 FastAPI 服务骨架，提供统一 API 入口，并满足以下最小验收：
- 服务启动成功
- Swagger 文档可访问
- 至少有健康检查接口
- 至少有一个创建批次接口

## 当前基础
- 已有领域模型：[src/domain/models.py](L:\Project\Python\task-forge\src\domain\models.py)
- 已有 Pydantic schema：[src/packages/core/schemas.py](L:\Project\Python\task-forge\src\packages\core\schemas.py)
- 已有 SQLAlchemy ORM：[src/packages/core/db/models.py](L:\Project\Python\task-forge\src\packages\core\db\models.py)
- 已有 PostgreSQL 连接：仓库根目录 `.env` 中存在 `DATABASE_URL`
- 当前缺失：
  - `fastapi`
  - `uvicorn`
  - API app 入口
  - routers 拆分
  - settings 模块
  - DB session 依赖注入

## 目标目录
建议把 API 服务放到 `src/apps/api` 下，结构如下：

```text
src/apps/api/
  app.py
  deps.py
  settings.py
  routers/
    __init__.py
    health.py
    task_batches.py
    tasks.py
    agents.py
    runs.py
    reviews.py
```

## 模块职责

### 1. app.py
职责：
- 初始化 `FastAPI` 应用
- 配置标题、版本、OpenAPI 路径
- 注册所有 router

建议：
- `title="TaskForge API"`
- `version="0.1.0"`
- 默认保留 `/docs` 和 `/openapi.json`

### 2. settings.py
职责：
- 统一读取环境变量
- 暴露 API 标题、版本、数据库连接串

建议字段：
- `app_name`
- `app_version`
- `debug`
- `database_url`

实现建议：
- 使用 `pydantic-settings` 或直接用 `pydantic.BaseModel + os.getenv`
- 先追求简单，不要过度抽象

### 3. deps.py
职责：
- 提供数据库 session 依赖

建议：
- 基于 `sqlalchemy.create_engine`
- 暴露 `get_db()` 生成器
- 路由层通过 `Depends(get_db)` 获取 `Session`

### 4. routers/health.py
职责：
- 提供健康检查

建议接口：
- `GET /health`

返回建议：
```json
{
  "status": "ok"
}
```

可选增强：
- 加一个简单数据库连通性检查

### 5. routers/task_batches.py
职责：
- 提供批次接口

第一阶段最小接口：
- `POST /task-batches`
- `GET /task-batches/{batch_id}`

`POST /task-batches` 行为：
- 接收 `TaskBatchCreate`
- 写入 `task_batches`
- 返回 `TaskBatchRead`

当前阶段不要一次把批次和任务创建逻辑做复杂。
先保证“能创建批次”。

### 6. routers/tasks.py
职责：
- 预留任务查询与创建入口

第一阶段可先放占位 router，不必完整实现。

建议后续接口：
- `POST /tasks`
- `GET /tasks/{task_id}`

### 7. routers/agents.py
职责：
- 预留角色查询与注册入口

建议后续接口：
- `POST /agents`
- `GET /agents/{agent_id}`

### 8. routers/runs.py
职责：
- 预留执行记录查询入口

建议后续接口：
- `GET /runs/{run_id}`

### 9. routers/reviews.py
职责：
- 预留人工审核查询入口

建议后续接口：
- `GET /reviews/{review_id}`

## 最小实现范围
本任务不要把所有业务一次做满，优先实现：

1. FastAPI 应用启动
2. `/health`
3. `/task-batches` 的创建接口
4. Swagger 文档可访问

这四项完成后，task01 就算达标。

## 接口设计

### GET /health
用途：
- 判断 API 服务是否在线

响应示例：
```json
{
  "status": "ok"
}
```

### POST /task-batches
用途：
- 创建一条批次记录

请求体：
- 使用 `TaskBatchCreate`

响应体：
- 使用 `TaskBatchRead`

处理流程：
1. 接收请求体
2. 构造 `TaskBatchORM`
3. 写入数据库
4. 提交事务
5. 返回创建后的对象

默认值建议：
- `status = "draft"` 或 `"submitted"`
- `total_tasks = 0`

建议当前先用 `draft`，因为此阶段只是在搭骨架。

## 代码实现建议

### settings
建议单例：
```python
settings = Settings()
```

### engine
建议在 `deps.py` 或 `db/session.py` 中复用已有 `DATABASE_URL`。

### schema 复用
优先复用：
- `TaskBatchCreate`
- `TaskBatchRead`

如果现有 schema 不够用，再补，不要重复定义一套。

### ORM 复用
直接复用：
- `TaskBatchORM`

## requirements 需要追加
当前 `requirements.txt` 还缺：
- `fastapi`
- `uvicorn[standard]`

如使用 `pydantic-settings`，还要追加：
- `pydantic-settings`

## 启动方式
实现完成后，建议支持：

```powershell
uvicorn src.apps.api.app:app --reload
```

文档访问地址：
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

健康检查：
- `http://127.0.0.1:8000/health`

## 验收步骤

### 1. 启动服务
执行：
```powershell
uvicorn src.apps.api.app:app --reload
```

### 2. 验证 OpenAPI
浏览器访问：
- `/docs`
- `/openapi.json`

### 3. 验证健康检查
请求：
```powershell
curl http://127.0.0.1:8000/health
```

期望：
- 返回 `{"status":"ok"}`

### 4. 验证创建批次
请求示例：
```powershell
curl -X POST http://127.0.0.1:8000/task-batches ^
  -H "Content-Type: application/json" ^
  -d "{\"title\":\"demo batch\",\"description\":\"first batch\",\"created_by\":\"ayu\",\"metadata\":{}}"
```

期望：
- 返回已创建批次
- 数据库 `task_batches` 中能查到记录

## 风险与约束
- 当前项目包路径是 `src/...`，不是标准安装包结构，导入路径要统一
- 当前已有 `src/domain` 和 `src/packages/core`，API 层不要再复制 schema 和 ORM
- 当前先做骨架，不要引入服务层、仓储层的大重构
- 当前数据库已可用，优先直连验证，后续再抽象

## 建议执行顺序
1. 更新 `requirements.txt`
2. 新建 `src/apps/api/settings.py`
3. 新建 `src/apps/api/deps.py`
4. 新建 `src/apps/api/routers/health.py`
5. 新建 `src/apps/api/routers/task_batches.py`
6. 新建 `src/apps/api/app.py`
7. 启动 `uvicorn`
8. 验证 `/docs`、`/health`、`POST /task-batches`

## 完成定义
当以下条件同时满足时，task01 完成：
- FastAPI 服务可启动
- `/docs` 可访问
- `/health` 正常返回
- `POST /task-batches` 可写入 PostgreSQL
