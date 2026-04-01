from __future__ import annotations


ERROR_CATEGORIES = {
    "timeout",
    "validation_error",
    "routing_error",
    "dependency_blocked",
    "execution_error",
    "external_tool_error",
}


def _normalize_text_parts(*parts: str | None, logs: list[str] | None = None) -> str:
    chunks = [part.strip().lower() for part in parts if part and part.strip()]
    if logs:
        chunks.extend(line.strip().lower() for line in logs if line and line.strip())
    return "\n".join(chunks)


def _is_timeout(text: str) -> bool:
    return "timed out" in text or "timeout" in text or "time out" in text


def _is_validation_error(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "validation",
            "must be a non-empty string",
            "output must be a dict",
            "invalid input",
            "invalid output",
        )
    )


def _is_external_tool_error(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "subprocess",
            "tool call",
            "external tool",
            "command failed",
            "process exited",
            "exit code",
        )
    )


def _is_routing_error(text: str) -> bool:
    return "no eligible agent role found" in text or "routing" in text and "no eligible" in text


def classify_run_error(
    *,
    run_status: str,
    error_message: str | None,
    logs: list[str] | None = None,
    routing_reason: str | None = None,
) -> str | None:
    if run_status != "failed":
        return None

    normalized = _normalize_text_parts(error_message, routing_reason, logs=logs)
    if _is_timeout(normalized):
        return "timeout"
    if _is_validation_error(normalized):
        return "validation_error"
    if _is_external_tool_error(normalized):
        return "external_tool_error"
    if _is_routing_error(normalized):
        return "routing_error"
    return "execution_error"


def classify_task_error(
    *,
    task_status: str,
    dependency_ids: list[str] | None = None,
    run_status: str | None = None,
    error_message: str | None = None,
    logs: list[str] | None = None,
    routing_reason: str | None = None,
    review_reason: str | None = None,
) -> str | None:
    dependency_ids = dependency_ids or []

    if task_status == "blocked" and dependency_ids:
        return "dependency_blocked"

    normalized_context = _normalize_text_parts(routing_reason, review_reason)
    if task_status in {"needs_review", "failed"} and _is_routing_error(normalized_context):
        return "routing_error"

    if run_status == "failed":
        return classify_run_error(
            run_status=run_status,
            error_message=error_message,
            logs=logs,
            routing_reason=routing_reason,
        )

    return None


def summarize_failure_categories(
    task_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in task_items:
        category = item.get("error_category")
        if not category:
            continue
        if category not in grouped:
            grouped[category] = {
                "category": category,
                "count": 0,
                "task_ids": [],
                "sample_messages": [],
            }
        grouped_item = grouped[category]
        grouped_item["count"] = int(grouped_item["count"]) + 1
        grouped_item["task_ids"].append(item["task_id"])
        message = item.get("error_message") or item.get("cancel_reason") or item.get("routing_reason")
        if message and message not in grouped_item["sample_messages"] and len(grouped_item["sample_messages"]) < 3:
            grouped_item["sample_messages"].append(message)

    ordered_categories = sorted(grouped.keys(), key=lambda value: (-int(grouped[value]["count"]), value))
    return [grouped[category] for category in ordered_categories]
