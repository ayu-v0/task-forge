from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_PRIMARY_INTENTS = {
    "coding",
    "research",
    "planning",
    "review",
    "writing",
    "testing",
    "general",
}
ALLOWED_TASK_TYPES = {
    "code",
    "worker_execute",
    "planner_preprocess",
    "reviewer_validate",
    "research_topic",
}
ALLOWED_ARTIFACT_TYPES = {
    "code_file",
    "code_patch",
    "document",
    "analysis_report",
    "test_report",
    "data_file",
    "json",
}
AUTO_TASK_TYPES = {"", "auto", "unknown", "task", "general", "misc"}

LANGUAGE_ALIASES = {
    "python": "python",
    "py": "python",
    "go": "go",
    "golang": "go",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "powershell": "powershell",
    "ps1": "powershell",
    "shell": "shell",
    "bash": "shell",
    "markdown": "markdown",
    "md": "markdown",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
}


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliverableContract(IntentModel):
    expected_artifact_types: list[str] = Field(default_factory=list)
    presentation_format: str | None = None
    file_extension: str | None = None
    include_code_block: bool = False
    require_file_level_artifact: bool = False
    allow_primary_json_only: bool = False

    @field_validator("expected_artifact_types", mode="before")
    @classmethod
    def _normalize_expected_types(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for item in values:
            artifact_type = str(item or "").strip().lower()
            if artifact_type in ALLOWED_ARTIFACT_TYPES and artifact_type not in normalized:
                normalized.append(artifact_type)
        return normalized or ["json"]

    @field_validator("presentation_format", "file_extension", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> str | None:
        text = str(value or "").strip().lower()
        return text or None


class RoutingHints(IntentModel):
    preferred_agent_roles: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    avoid_agent_roles: list[str] = Field(default_factory=list)

    @field_validator("preferred_agent_roles", "required_capabilities", "avoid_agent_roles", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized


class TaskIntent(IntentModel):
    primary_intent: str
    task_type: str
    confidence: float = Field(ge=0, le=1)
    language: str | None = None
    subject: str | None = None
    operation: str | None = None
    deliverable_contract: DeliverableContract
    routing_hints: RoutingHints = Field(default_factory=RoutingHints)
    warnings: list[str] = Field(default_factory=list)
    source: str = "model"

    @field_validator("primary_intent", mode="before")
    @classmethod
    def _normalize_primary_intent(cls, value: Any) -> str:
        primary_intent = str(value or "").strip().lower()
        return primary_intent if primary_intent in ALLOWED_PRIMARY_INTENTS else "general"

    @field_validator("task_type", mode="before")
    @classmethod
    def _normalize_task_type(cls, value: Any) -> str:
        task_type = str(value or "").strip().lower()
        return task_type if task_type in ALLOWED_TASK_TYPES else "worker_execute"

    @field_validator("language", mode="before")
    @classmethod
    def _normalize_language(cls, value: Any) -> str | None:
        language = str(value or "").strip().lower()
        return LANGUAGE_ALIASES.get(language, language) if language else None

    @field_validator("subject", "operation", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @field_validator("warnings", mode="before")
    @classmethod
    def _normalize_warnings(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in values if str(item).strip()]


def is_auto_task_type(value: Any) -> bool:
    return str(value or "").strip().lower() in AUTO_TASK_TYPES


def task_text_from_payload(task: dict[str, Any]) -> str:
    payload = task.get("input_payload") if isinstance(task.get("input_payload"), dict) else {}
    parts = [
        task.get("title"),
        task.get("description"),
        payload.get("prompt"),
        payload.get("text"),
        payload.get("content"),
    ]
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _detect_language(text: str) -> str | None:
    language_patterns = [
        ("typescript", [r"\btypescript\b", r"\bts\b", "type script"]),
        ("javascript", [r"\bjavascript\b", r"\bjs\b", "java script"]),
        ("powershell", [r"\bpowershell\b", r"\bps1\b"]),
        ("python", [r"\bpython\b", r"\bpy\b", "python代码"]),
        ("go", [r"\bgolang\b", r"\bgo\b", "go语言", "go代码"]),
        ("shell", [r"\bbash\b", r"\bshell\b", r"\bsh\b"]),
        ("markdown", [r"\bmarkdown\b", r"\bmd\b", "markdown"]),
        ("html", [r"\bhtml\b"]),
        ("css", [r"\bcss\b"]),
        ("json", [r"\bjson\b"]),
        ("yaml", [r"\byaml\b", r"\byml\b"]),
    ]
    for language, patterns in language_patterns:
        if any(re.search(pattern, text) for pattern in patterns):
            return language
    return None


def _file_extension_for(language: str | None, presentation_format: str | None = None) -> str | None:
    if presentation_format == "markdown":
        return ".md"
    return {
        "python": ".py",
        "go": ".go",
        "javascript": ".js",
        "typescript": ".ts",
        "powershell": ".ps1",
        "shell": ".sh",
        "markdown": ".md",
        "html": ".html",
        "css": ".css",
        "json": ".json",
        "yaml": ".yaml",
    }.get(language or "")


def _routing_for(primary_intent: str, task_type: str) -> RoutingHints:
    if primary_intent == "coding" or task_type == "code":
        return RoutingHints(preferred_agent_roles=["code_agent"], required_capabilities=["task:code"])
    if primary_intent == "research" or task_type == "research_topic":
        return RoutingHints(preferred_agent_roles=["search_agent"], required_capabilities=["task:research_topic"])
    if primary_intent == "planning" or task_type == "planner_preprocess":
        return RoutingHints(preferred_agent_roles=["planner_agent"], required_capabilities=["task:planner_preprocess"])
    if primary_intent == "review" or task_type == "reviewer_validate":
        return RoutingHints(preferred_agent_roles=["reviewer_agent"], required_capabilities=["task:reviewer_validate"])
    return RoutingHints(preferred_agent_roles=["worker_agent"], required_capabilities=[f"task:{task_type}"])


def _contract_for(text: str, primary_intent: str, language: str | None) -> DeliverableContract:
    wants_markdown = _contains_any(text, ["markdown", "md格式", "markdown形式", "markdown 格式", "markdown 形式", "以md", "以 markdown"])
    wants_patch = _contains_any(text, ["patch", "diff", "补丁", "差异"])
    wants_test_report = _contains_any(text, ["测试报告", "test report", "测试结果"])
    wants_file = _contains_any(text, ["文件", "file", "保存为", "生成一个", ".py", ".go", ".js", ".ts", ".md"])

    artifact_types: list[str]
    if wants_patch:
        artifact_types = ["code_patch"]
    elif wants_markdown:
        artifact_types = ["document"]
    elif primary_intent == "coding":
        artifact_types = ["code_file"]
    elif primary_intent == "research":
        artifact_types = ["analysis_report"]
    elif primary_intent in {"writing", "planning"}:
        artifact_types = ["document"]
    else:
        artifact_types = ["json"]

    if wants_test_report and "test_report" not in artifact_types:
        artifact_types.append("test_report")

    presentation_format = "markdown" if wants_markdown or primary_intent == "writing" else None
    include_code_block = bool(primary_intent == "coding" and presentation_format == "markdown")
    require_file = bool(primary_intent == "coding" and "code_file" in artifact_types and not presentation_format)
    return DeliverableContract(
        expected_artifact_types=artifact_types,
        presentation_format=presentation_format,
        file_extension=_file_extension_for(language, presentation_format) if (wants_file or presentation_format or primary_intent == "writing") else None,
        include_code_block=include_code_block,
        require_file_level_artifact=require_file,
        allow_primary_json_only=False,
    )


def rule_based_intent(task_or_text: dict[str, Any] | str, *, provided_task_type: str | None = None) -> TaskIntent:
    raw_text = task_text_from_payload(task_or_text) if isinstance(task_or_text, dict) else str(task_or_text or "")
    text = raw_text.lower()
    provided = (provided_task_type or (task_or_text.get("task_type") if isinstance(task_or_text, dict) else "") or "").strip().lower()

    code_keywords = [
        "code",
        "function",
        "script",
        "bug",
        "refactor",
        "代码",
        "函数",
        "脚本",
        "实现",
        "修复",
    ]
    research_keywords = ["research", "search", "查", "查询", "搜索", "调研", "了解"]
    planning_keywords = ["plan", "方案", "计划", "拆解", "规划"]
    review_keywords = ["review", "审查", "检查", "评审"]
    writing_keywords = [
        "readme",
        "markdown",
        "文档",
        "报告",
        "说明",
        "作文",
        "文章",
        "文案",
        "故事",
        "小说",
        "演讲稿",
        "读后感",
        "观后感",
        "周记",
        "日记",
        "总结",
    ]
    testing_keywords = ["test", "测试"]

    if _contains_any(text, code_keywords):
        primary_intent = "coding"
        task_type = "code"
    elif _contains_any(text, research_keywords):
        primary_intent = "research"
        task_type = "research_topic"
    elif _contains_any(text, planning_keywords):
        primary_intent = "planning"
        task_type = "planner_preprocess"
    elif _contains_any(text, review_keywords):
        primary_intent = "review"
        task_type = "reviewer_validate"
    elif _contains_any(text, testing_keywords):
        primary_intent = "testing"
        task_type = "code"
    elif _contains_any(text, writing_keywords):
        primary_intent = "writing"
        task_type = "worker_execute"
    else:
        primary_intent = "general"
        task_type = "worker_execute"

    language = _detect_language(text)
    contract = _contract_for(text, primary_intent, language)
    warnings: list[str] = []
    if provided and not is_auto_task_type(provided) and provided in ALLOWED_TASK_TYPES and provided != task_type:
        warnings.append(f"explicit_task_type_conflict: provided={provided}, recognized={task_type}")

    return TaskIntent(
        primary_intent=primary_intent,
        task_type=task_type,
        confidence=0.64 if primary_intent != "general" else 0.42,
        language=language,
        subject=raw_text[:200] or None,
        operation="classify",
        deliverable_contract=contract,
        routing_hints=_routing_for(primary_intent, task_type),
        warnings=warnings,
        source="rules_fallback",
    )


def normalize_model_intent_payload(
    payload: dict[str, Any],
    *,
    fallback_text: str = "",
    provided_task_type: str | None = None,
) -> TaskIntent:
    intent = TaskIntent.model_validate(payload)
    if not intent.deliverable_contract.expected_artifact_types:
        fallback = rule_based_intent(fallback_text, provided_task_type=provided_task_type)
        intent.deliverable_contract = fallback.deliverable_contract
    if intent.confidence < 0.5:
        intent.warnings.append("low_intent_confidence")
    return intent
