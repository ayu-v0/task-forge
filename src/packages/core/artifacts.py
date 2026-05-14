from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from src.packages.core.token_budget import build_result_summary, summarize_value


ARTIFACT_SCHEMA_VERSION = "artifact.v1"
ARTIFACT_ROLE_PRIMARY_OUTPUT = "primary_output"
ARTIFACT_ROLE_FINAL_DELIVERABLE = "final_deliverable"
DEFAULT_ARTIFACT_CONTENT_TYPE = "application/json"
TEXT_PREVIEW_LIMIT = 1200

LANGUAGE_CONTENT_TYPES = {
    "python": "text/x-python",
    "py": "text/x-python",
    "javascript": "text/javascript",
    "js": "text/javascript",
    "typescript": "text/typescript",
    "ts": "text/typescript",
    "json": "application/json",
    "markdown": "text/markdown",
    "md": "text/markdown",
    "html": "text/html",
    "css": "text/css",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "powershell": "text/x-powershell",
    "ps1": "text/x-powershell",
    "shell": "text/x-shellscript",
    "sh": "text/x-shellscript",
    "go": "text/x-go",
    "golang": "text/x-go",
}

EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ps1": "powershell",
    ".sh": "shell",
    ".go": "go",
}

LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "json": ".json",
    "markdown": ".md",
    "md": ".md",
    "html": ".html",
    "css": ".css",
    "yaml": ".yaml",
    "yml": ".yml",
    "powershell": ".ps1",
    "ps1": ".ps1",
    "shell": ".sh",
    "sh": ".sh",
    "go": ".go",
    "golang": ".go",
}

DELIVERABLE_TYPE_EXTENSIONS = {
    "markdown": ".md",
    "txt": ".txt",
    "code": ".txt",
    "json": ".json",
}

ALLOWED_DELIVERABLE_TYPES = set(DELIVERABLE_TYPE_EXTENSIONS)


def infer_artifact_type(output_snapshot: dict[str, Any]) -> str:
    if output_snapshot.get("stage") == "reviewer":
        return "review_note"
    if "code_plan" in output_snapshot:
        return "generic_result"
    if "search_plan" in output_snapshot or output_snapshot.get("stage") == "search":
        return "report"
    if isinstance(output_snapshot, dict):
        return "json"
    return "text"


def build_structured_output(output_snapshot: dict[str, Any]) -> dict[str, Any]:
    preview = summarize_value(output_snapshot)
    structured = preview if isinstance(preview, dict) else {"value": preview}
    return {
        **structured,
        "keys": sorted(output_snapshot.keys()),
        "field_count": len(output_snapshot),
        "preview": preview,
    }


def _preview_text(value: Any, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}..."


def _line_count(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    return len(text.splitlines()) or 1


def _is_safe_relative_path(path: str) -> bool:
    value = path.strip().replace("\\", "/")
    if not value:
        return False
    if PureWindowsPath(path).is_absolute() or PurePosixPath(value).is_absolute():
        return False
    return ".." not in PurePosixPath(value).parts


def _has_path_traversal(path: str) -> bool:
    return ".." in PurePosixPath(path.strip().replace("\\", "/")).parts


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _safe_generated_path(task_id: str, path: str, language: Any) -> str:
    normalized = _normalize_path(path)
    basename = PurePosixPath(normalized).name
    if basename and basename not in {".", ".."}:
        return f"generated/{basename}"
    normalized_language = str(language or "").strip().lower()
    extension = LANGUAGE_EXTENSIONS.get(normalized_language, ".txt")
    return f"generated/{task_id}{extension}"


def _language_for_path(path: str, language: Any) -> str:
    explicit = str(language or "").strip().lower()
    if explicit:
        return explicit
    suffix = PurePosixPath(path).suffix.lower()
    return EXTENSION_LANGUAGES.get(suffix, suffix.lstrip(".") or "text")


def _content_type_for(path: str, language: Any, fallback: str = "text/plain") -> str:
    normalized_language = _language_for_path(path, language)
    if normalized_language in LANGUAGE_CONTENT_TYPES:
        return LANGUAGE_CONTENT_TYPES[normalized_language]
    suffix = PurePosixPath(path).suffix.lower()
    return LANGUAGE_CONTENT_TYPES.get(suffix.lstrip("."), fallback)


def _normalize_deliverable_type(value: Any) -> str | None:
    deliverable_type = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "md": "markdown",
        "mark_down": "markdown",
        "text": "txt",
        "plain": "txt",
        "plain_text": "txt",
        "source": "code",
        "source_code": "code",
    }
    deliverable_type = aliases.get(deliverable_type, deliverable_type)
    return deliverable_type if deliverable_type in ALLOWED_DELIVERABLE_TYPES else None


def _deliverable_type_from_contract(input_snapshot: dict[str, Any] | None) -> str | None:
    contract = _contract_from_input(input_snapshot)
    return _normalize_deliverable_type(contract.get("deliverable_type"))


def _deliverable_type_for(
    *,
    artifact_type: str,
    path: str = "",
    language: Any = None,
    deliverable: dict[str, Any] | None = None,
    input_snapshot: dict[str, Any] | None = None,
) -> str:
    explicit = _normalize_deliverable_type((deliverable or {}).get("deliverable_type"))
    if explicit:
        return explicit
    contract_type = _deliverable_type_from_contract(input_snapshot)
    if contract_type:
        return contract_type
    if artifact_type in {"code_file", "code_patch"}:
        return "code"
    if artifact_type == "json":
        return "json"
    normalized_language = _language_for_path(path, language)
    if normalized_language in {"markdown", "md"}:
        return "markdown"
    if PurePosixPath(path).suffix.lower() == ".txt":
        return "txt"
    if artifact_type in {"document", "analysis_report"}:
        return "markdown"
    return "txt"


def _extract_deliverables_from(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def extract_deliverables(output_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [output_snapshot.get("deliverables")]
    result = output_snapshot.get("result")
    if isinstance(result, dict):
        candidates.append(result.get("deliverables"))

    for candidate in candidates:
        deliverables = _extract_deliverables_from(candidate)
        if deliverables:
            return deliverables
    return []


def _generic_deliverable_payload(
    *,
    task_id: str,
    run_id: str,
    deliverable: dict[str, Any],
    index: int,
    warning: str | None = None,
) -> dict[str, Any]:
    raw_content = dict(deliverable)
    metadata = {
        "source": "worker",
        "artifact_role": ARTIFACT_ROLE_FINAL_DELIVERABLE,
        "deliverable_index": index,
        "original_type": str(deliverable.get("type", "generic_result")),
        "deliverable_type": _deliverable_type_for(artifact_type="generic_result", deliverable=deliverable),
    }
    if warning:
        metadata["warning"] = warning
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": "generic_result",
        "uri": f"memory://runs/{run_id}/deliverables/{index}",
        "content_type": DEFAULT_ARTIFACT_CONTENT_TYPE,
        "raw_content": raw_content,
        "summary": build_result_summary(raw_content),
        "structured_output": build_structured_output(raw_content),
        "metadata": metadata,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def _code_file_payload(task_id: str, run_id: str, deliverable: dict[str, Any], index: int) -> dict[str, Any]:
    path = _normalize_path(str(deliverable.get("path", "")))
    content = str(deliverable.get("content", ""))
    if _has_path_traversal(path) or not content:
        return _generic_deliverable_payload(
            task_id=task_id,
            run_id=run_id,
            deliverable=deliverable,
            index=index,
            warning="invalid_code_file_deliverable",
        )
    path_sanitized = False
    if not _is_safe_relative_path(path):
        path = _safe_generated_path(task_id, path, deliverable.get("language"))
        path_sanitized = True
    language = _language_for_path(path, deliverable.get("language"))
    change_type = str(deliverable.get("change_type", "modified") or "modified")
    metadata = {
        "source": "worker",
        "artifact_role": ARTIFACT_ROLE_FINAL_DELIVERABLE,
        "deliverable_index": index,
        "deliverable_type": _deliverable_type_for(
            artifact_type="code_file",
            path=path,
            language=deliverable.get("language"),
            deliverable=deliverable,
        ),
    }
    if path_sanitized:
        metadata["warning"] = "sanitized_code_file_path"
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": "code_file",
        "uri": f"workspace://{path}",
        "content_type": _content_type_for(path, language),
        "raw_content": {"path": path, "content": content},
        "summary": {"path": path, "language": language, "change_type": change_type},
        "structured_output": {
            "path": path,
            "language": language,
            "change_type": change_type,
            "deliverable_type": metadata["deliverable_type"],
            "line_count": _line_count(content),
            "content_preview": _preview_text(content),
        },
        "metadata": metadata,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def _code_patch_payload(task_id: str, run_id: str, deliverable: dict[str, Any], index: int) -> dict[str, Any]:
    diff = str(deliverable.get("diff", ""))
    files_changed = deliverable.get("files_changed", [])
    if not isinstance(files_changed, list):
        files_changed = []
    normalized_files = [_normalize_path(str(path)) for path in files_changed if _is_safe_relative_path(str(path))]
    if not diff:
        return _generic_deliverable_payload(
            task_id=task_id,
            run_id=run_id,
            deliverable=deliverable,
            index=index,
            warning="invalid_code_patch_deliverable",
        )
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": "code_patch",
        "uri": f"patch://{task_id}/{run_id}/{index}",
        "content_type": "text/x-diff",
        "raw_content": {"diff": diff},
        "summary": {
            "files_changed": normalized_files,
            "insertions": int(deliverable.get("insertions", 0) or 0),
            "deletions": int(deliverable.get("deletions", 0) or 0),
        },
        "structured_output": {
            "files_changed": normalized_files,
            "file_count": len(normalized_files),
            "diff_preview": _preview_text(diff),
        },
        "metadata": {
            "source": "worker",
            "artifact_role": ARTIFACT_ROLE_FINAL_DELIVERABLE,
            "deliverable_index": index,
            "deliverable_type": _deliverable_type_for(artifact_type="code_patch", deliverable=deliverable),
        },
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def _test_report_payload(task_id: str, run_id: str, deliverable: dict[str, Any], index: int) -> dict[str, Any]:
    command = str(deliverable.get("command", ""))
    status = str(deliverable.get("status", "unknown") or "unknown")
    output = str(deliverable.get("output", ""))
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": "test_report",
        "uri": f"test-report://{task_id}/{run_id}/{index}",
        "content_type": "text/plain",
        "raw_content": {"command": command, "status": status, "output": output},
        "summary": {"command": command, "status": status},
        "structured_output": {"command": command, "status": status, "output_preview": _preview_text(output)},
        "metadata": {
            "source": "worker",
            "artifact_role": ARTIFACT_ROLE_FINAL_DELIVERABLE,
            "deliverable_index": index,
            "deliverable_type": _deliverable_type_for(artifact_type="test_report", deliverable=deliverable),
        },
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def _document_like_payload(
    *,
    task_id: str,
    run_id: str,
    deliverable: dict[str, Any],
    index: int,
    artifact_type: str,
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _normalize_path(str(deliverable.get("path", "")))
    title = str(deliverable.get("title", path or artifact_type))
    content = str(deliverable.get("content", deliverable.get("body", "")))
    uri = f"{artifact_type}://{task_id}/{run_id}/{index}"
    if path and _is_safe_relative_path(path):
        uri = f"workspace://{path}"
    deliverable_type = _deliverable_type_for(
        artifact_type=artifact_type,
        path=path,
        language=deliverable.get("language"),
        deliverable=deliverable,
        input_snapshot=input_snapshot,
    )
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "uri": uri,
        "content_type": _content_type_for(path, deliverable.get("language"), "text/plain"),
        "raw_content": {"path": path, "title": title, "content": content},
        "summary": {"path": path, "title": title, "deliverable_type": deliverable_type},
        "structured_output": {"path": path, "title": title, "deliverable_type": deliverable_type, "content_preview": _preview_text(content)},
        "metadata": {
            "source": "worker",
            "artifact_role": ARTIFACT_ROLE_FINAL_DELIVERABLE,
            "deliverable_index": index,
            "deliverable_type": deliverable_type,
        },
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def build_deliverable_artifact_payloads(
    *,
    task_id: str,
    run_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    deliverables = extract_deliverables(output_snapshot)
    if not deliverables:
        deliverables = _infer_contract_deliverables(task_id, output_snapshot, input_snapshot)
    for index, deliverable in enumerate(deliverables, start=1):
        deliverable_type = str(deliverable.get("type", deliverable.get("artifact_type", "generic_result"))).strip()
        if deliverable_type == "code_file":
            payloads.append(_code_file_payload(task_id, run_id, deliverable, index))
        elif deliverable_type == "code_patch":
            payloads.append(_code_patch_payload(task_id, run_id, deliverable, index))
        elif deliverable_type == "test_report":
            payloads.append(_test_report_payload(task_id, run_id, deliverable, index))
        elif deliverable_type in {"document", "analysis_report", "data_file"}:
            payloads.append(
                _document_like_payload(
                    task_id=task_id,
                    run_id=run_id,
                    deliverable=deliverable,
                    index=index,
                    artifact_type=deliverable_type,
                    input_snapshot=input_snapshot,
                )
            )
        else:
            payloads.append(_generic_deliverable_payload(task_id=task_id, run_id=run_id, deliverable=deliverable, index=index))
    return payloads


def _contract_from_input(input_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(input_snapshot, dict):
        return {}
    contract = input_snapshot.get("deliverable_contract")
    return contract if isinstance(contract, dict) else {}


def _contract_expected_types(input_snapshot: dict[str, Any] | None) -> set[str]:
    contract = _contract_from_input(input_snapshot)
    expected = contract.get("expected_artifact_types")
    if not isinstance(expected, list):
        return set()
    return {str(item).strip() for item in expected if str(item).strip()}


def _summary_text(output_snapshot: dict[str, Any]) -> str:
    summary = output_snapshot.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    result = output_snapshot.get("result")
    if isinstance(result, dict):
        nested_summary = result.get("summary")
        if isinstance(nested_summary, str) and nested_summary.strip():
            return nested_summary.strip()
    return ""


def _document_from_code_result(
    *,
    task_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None,
    code_result: dict[str, Any],
) -> dict[str, Any]:
    contract = _contract_from_input(input_snapshot)
    deliverable_type = _normalize_deliverable_type(contract.get("deliverable_type")) or "markdown"
    language = str(
        code_result.get("language")
        or output_snapshot.get("language")
        or (input_snapshot or {}).get("language")
        or ""
    ).strip().lower()
    code = str(code_result.get("code") or "")
    fence_language = language if language and language != "text" else ""
    title = _summary_text(output_snapshot) or "Generated code"
    path = contract.get("file_extension")
    if not isinstance(path, str) or not path.strip():
        path = DELIVERABLE_TYPE_EXTENSIONS.get(deliverable_type, ".md")
    extension = path if path.startswith(".") else f".{path}"
    if deliverable_type == "txt":
        return {
            "type": "document",
            "deliverable_type": "txt",
            "path": f"generated/{task_id}{extension}",
            "title": title,
            "language": "text",
            "content": f"{code.rstrip()}\n",
            "inferred_from": "deliverable_contract.result.code",
        }
    return {
        "type": "document",
        "deliverable_type": "markdown",
        "path": f"generated/{task_id}{extension}",
        "title": title,
        "language": "markdown",
        "content": f"```{fence_language}\n{code.rstrip()}\n```\n",
        "inferred_from": "deliverable_contract.result.code",
    }


def _document_from_summary(
    task_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    summary = _summary_text(output_snapshot)
    if not summary:
        return []
    contract = _contract_from_input(input_snapshot)
    deliverable_type = _normalize_deliverable_type(contract.get("deliverable_type")) or "markdown"
    extension = str(contract.get("file_extension") or DELIVERABLE_TYPE_EXTENSIONS[deliverable_type])
    extension = extension if extension.startswith(".") else f".{extension}"
    return [
        {
            "type": "document",
            "deliverable_type": deliverable_type,
            "path": f"generated/{task_id}{extension}",
            "title": "Generated summary",
            "language": "markdown" if deliverable_type == "markdown" else "text",
            "content": summary,
            "inferred_from": "deliverable_contract.summary",
        }
    ]


def _document_from_content(
    task_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    content = output_snapshot.get("content")
    result = output_snapshot.get("result")
    if not isinstance(content, str) or not content.strip():
        if isinstance(result, dict):
            nested_content = result.get("content") or result.get("body") or result.get("text")
            content = nested_content if isinstance(nested_content, str) else ""
    if not isinstance(content, str) or not content.strip():
        return []

    title = _summary_text(output_snapshot) or "Generated document"
    contract = _contract_from_input(input_snapshot)
    deliverable_type = _normalize_deliverable_type(contract.get("deliverable_type")) or "markdown"
    extension = str(contract.get("file_extension") or DELIVERABLE_TYPE_EXTENSIONS[deliverable_type])
    extension = extension if extension.startswith(".") else f".{extension}"
    return [
        {
            "type": "document",
            "deliverable_type": deliverable_type,
            "path": f"generated/{task_id}{extension}",
            "title": title,
            "language": "markdown" if deliverable_type == "markdown" else "text",
            "content": content,
            "inferred_from": "deliverable_contract.result.content",
        }
    ]


def _infer_contract_deliverables(
    task_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    expected_types = _contract_expected_types(input_snapshot)
    if not expected_types:
        return _infer_legacy_code_deliverables(task_id, output_snapshot)

    if "code_patch" in expected_types and expected_types <= {"code_patch"}:
        return []

    result = output_snapshot.get("result")
    code_result = _find_legacy_code_result(result) if isinstance(result, dict) else None
    if code_result is not None:
        inferred: list[dict[str, Any]] = []
        if "document" in expected_types:
            inferred.append(
                _document_from_code_result(
                    task_id=task_id,
                    output_snapshot=output_snapshot,
                    input_snapshot=input_snapshot,
                    code_result=code_result,
                )
            )
        if "code_file" in expected_types:
            inferred.extend(_infer_legacy_code_deliverables(task_id, output_snapshot))
        if inferred:
            return inferred

    if "document" in expected_types:
        content_deliverables = _document_from_content(task_id, output_snapshot, input_snapshot)
        if content_deliverables:
            return content_deliverables
        return _document_from_summary(task_id, output_snapshot, input_snapshot)
    return _infer_legacy_code_deliverables(task_id, output_snapshot)


def _find_legacy_code_result(value: dict[str, Any], depth: int = 0) -> dict[str, Any] | None:
    code = value.get("code")
    if isinstance(code, str) and code.strip():
        return value
    if depth >= 3:
        return None
    for nested_key in ("result", "output", "artifact"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            found = _find_legacy_code_result(nested, depth + 1)
            if found is not None:
                return found
    return None


def _infer_legacy_code_deliverables(task_id: str, output_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result = output_snapshot.get("result")
    if not isinstance(result, dict):
        return []
    code_result = _find_legacy_code_result(result)
    if code_result is None:
        return []
    code = code_result.get("code")
    if not isinstance(code, str) or not code.strip():
        return []

    language = str(code_result.get("language") or output_snapshot.get("language") or "text").strip().lower()
    explicit_path = code_result.get("path") or code_result.get("file_path") or code_result.get("filename")
    if isinstance(explicit_path, str) and _is_safe_relative_path(explicit_path):
        path = _normalize_path(explicit_path)
    else:
        extension = LANGUAGE_EXTENSIONS.get(language, ".txt")
        path = f"generated/{task_id}{extension}"

    return [
        {
            "type": "code_file",
            "path": path,
            "language": language,
            "change_type": "created",
            "content": code,
            "inferred_from": "result.code",
        }
    ]


def build_primary_artifact_payload(
    *,
    task_id: str,
    run_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_type = infer_artifact_type(output_snapshot)
    deliverable_type = _deliverable_type_for(artifact_type=artifact_type, input_snapshot=input_snapshot)
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "uri": f"memory://artifacts/{run_id}/primary-output",
        "content_type": DEFAULT_ARTIFACT_CONTENT_TYPE,
        "raw_content": output_snapshot,
        "summary": build_result_summary(output_snapshot),
        "structured_output": build_structured_output(output_snapshot),
        "metadata": {
            "source": "worker",
            "artifact_role": ARTIFACT_ROLE_PRIMARY_OUTPUT,
            "artifact_type": artifact_type,
            "deliverable_type": deliverable_type,
        },
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def build_artifact_payloads(
    *,
    task_id: str,
    run_id: str,
    output_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        build_primary_artifact_payload(
            task_id=task_id,
            run_id=run_id,
            output_snapshot=output_snapshot,
            input_snapshot=input_snapshot,
        ),
        *build_deliverable_artifact_payloads(
            task_id=task_id,
            run_id=run_id,
            output_snapshot=output_snapshot,
            input_snapshot=input_snapshot,
        ),
    ]


def build_artifact_payload(
    *,
    task_id: str,
    run_id: str,
    output_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return build_primary_artifact_payload(task_id=task_id, run_id=run_id, output_snapshot=output_snapshot)
