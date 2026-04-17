from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import engine
from src.packages.core.db.models import AgentRoleORM


STRUCTURED_OUTPUT_CONTRACT = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "result": {"type": "object"},
        "warnings": {"type": "array"},
        "next_action_hint": {"type": ["string", "null"]},
    },
}


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
            "prompt_budget_policy": {
                "template_name": "worker",
                "model_context_limit": 128000,
                "max_global_background_tokens": 256,
                "max_task_input_tokens": 4096,
                "max_dependency_summary_tokens": 768,
                "max_result_summary_tokens": 256,
                "max_validation_rule_tokens": 512,
                "max_history_background_tokens": 128,
                "reserved_output_tokens": 512,
            },
        },
        "output_schema": {"output_contract": STRUCTURED_OUTPUT_CONTRACT},
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
            "prompt_budget_policy": {
                "template_name": "worker",
                "model_context_limit": 128000,
                "max_global_background_tokens": 256,
                "max_task_input_tokens": 4096,
                "max_dependency_summary_tokens": 768,
                "max_result_summary_tokens": 256,
                "max_validation_rule_tokens": 512,
                "max_history_background_tokens": 128,
                "reserved_output_tokens": 768,
            },
        },
        "output_schema": {"output_contract": STRUCTURED_OUTPUT_CONTRACT},
    },
    {
        "role_name": "planner_agent",
        "description": "Built-in planner for demo preprocessing",
        "capabilities": ["task:planner_preprocess"],
        "input_schema": {
            "supported_task_types": ["planner_preprocess"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
            "prompt_budget_policy": {
                "template_name": "planner",
                "model_context_limit": 128000,
                "max_global_background_tokens": 2048,
                "max_task_input_tokens": 2048,
                "max_dependency_summary_tokens": 256,
                "max_result_summary_tokens": 128,
                "max_validation_rule_tokens": 512,
                "max_history_background_tokens": 256,
                "reserved_output_tokens": 1024,
            },
        },
        "output_schema": {"output_contract": STRUCTURED_OUTPUT_CONTRACT},
    },
    {
        "role_name": "worker_agent",
        "description": "Built-in worker for demo execution",
        "capabilities": ["task:worker_execute"],
        "input_schema": {
            "supported_task_types": ["worker_execute"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
            "prompt_budget_policy": {
                "template_name": "worker",
                "model_context_limit": 128000,
                "max_global_background_tokens": 256,
                "max_task_input_tokens": 4096,
                "max_dependency_summary_tokens": 768,
                "max_result_summary_tokens": 256,
                "max_validation_rule_tokens": 512,
                "max_history_background_tokens": 128,
                "reserved_output_tokens": 768,
            },
        },
        "output_schema": {"output_contract": STRUCTURED_OUTPUT_CONTRACT},
    },
    {
        "role_name": "reviewer_agent",
        "description": "Built-in reviewer for demo validation",
        "capabilities": ["task:reviewer_validate"],
        "input_schema": {
            "supported_task_types": ["reviewer_validate"],
            "supports_concurrency": True,
            "allows_auto_retry": False,
            "prompt_budget_policy": {
                "template_name": "reviewer",
                "model_context_limit": 128000,
                "max_global_background_tokens": 256,
                "max_task_input_tokens": 1024,
                "max_dependency_summary_tokens": 256,
                "max_result_summary_tokens": 4096,
                "max_validation_rule_tokens": 2048,
                "max_history_background_tokens": 64,
                "reserved_output_tokens": 512,
            },
        },
        "output_schema": {"output_contract": STRUCTURED_OUTPUT_CONTRACT},
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
