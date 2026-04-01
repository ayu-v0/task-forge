from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import engine
from src.packages.core.db.models import AgentRoleORM


BUILTIN_ROLES: tuple[dict, ...] = (
    {
        "role_name": "search_agent",
        "description": "Built-in search agent for research-oriented tasks",
        "capabilities": ["task:search", "task:research_topic"],
        "input_schema": {
            "supported_task_types": [],
            "input_requirements": {"properties": {"query": {"type": "string"}}},
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "output_schema": {"output_contract": {"type": "object"}},
    },
    {
        "role_name": "code_agent",
        "description": "Built-in code agent for implementation-oriented tasks",
        "capabilities": ["task:code", "task:implement_feature"],
        "input_schema": {
            "supported_task_types": [],
            "input_requirements": {
                "properties": {
                    "prompt": {"type": "string"},
                    "language": {"type": "string"},
                }
            },
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "output_schema": {"output_contract": {"type": "object"}},
    },
    {
        "role_name": "planner_agent",
        "description": "Built-in planner for demo preprocessing",
        "capabilities": ["task:planner_preprocess"],
        "input_schema": {
            "supported_task_types": ["planner_preprocess"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "output_schema": {},
    },
    {
        "role_name": "worker_agent",
        "description": "Built-in worker for demo execution",
        "capabilities": ["task:worker_execute"],
        "input_schema": {
            "supported_task_types": ["worker_execute"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "output_schema": {},
    },
    {
        "role_name": "reviewer_agent",
        "description": "Built-in reviewer for demo validation",
        "capabilities": ["task:reviewer_validate"],
        "input_schema": {
            "supported_task_types": ["reviewer_validate"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "output_schema": {},
    },
)


def ensure_builtin_agent_roles() -> None:
    with Session(engine) as session:
        with session.begin():
            for config in BUILTIN_ROLES:
                existing = session.scalar(
                    select(AgentRoleORM).where(AgentRoleORM.role_name == config["role_name"])
                )
                if existing is not None:
                    existing.description = config["description"]
                    existing.capabilities = config["capabilities"]
                    existing.input_schema = config["input_schema"]
                    existing.output_schema = config["output_schema"]
                    existing.timeout_seconds = 300
                    existing.max_retries = 0
                    existing.enabled = True
                    existing.version = "1.0.0"
                    continue
                session.add(
                    AgentRoleORM(
                        role_name=config["role_name"],
                        description=config["description"],
                        capabilities=config["capabilities"],
                        input_schema=config["input_schema"],
                        output_schema=config["output_schema"],
                        timeout_seconds=300,
                        max_retries=0,
                        enabled=True,
                        version="1.0.0",
                    )
                )
