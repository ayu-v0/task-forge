from __future__ import annotations

from src.packages.core.artifacts import build_artifact_payloads, extract_deliverables


def test_build_artifact_payloads_creates_code_file_patch_and_test_report() -> None:
    output_snapshot = {
        "status": "ok",
        "result": {
            "deliverables": [
                {
                    "type": "code_file",
                    "path": "src/foo.py",
                    "language": "python",
                    "change_type": "created",
                    "content": "print('ok')\n",
                },
                {
                    "type": "code_patch",
                    "files_changed": ["src/foo.py"],
                    "insertions": 2,
                    "deletions": 1,
                    "diff": "diff --git a/src/foo.py b/src/foo.py\n",
                },
                {
                    "type": "test_report",
                    "command": "pytest src/tests/test_foo.py",
                    "status": "passed",
                    "output": "1 passed",
                },
            ]
        },
    }

    payloads = build_artifact_payloads(task_id="task_1", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == [
        "json",
        "code_file",
        "code_patch",
        "test_report",
    ]
    code_file = payloads[1]
    assert code_file["uri"] == "workspace://src/foo.py"
    assert code_file["content_type"] == "text/x-python"
    assert code_file["summary"]["path"] == "src/foo.py"
    assert code_file["raw_content"]["content"] == "print('ok')\n"
    assert code_file["metadata"]["artifact_role"] == "final_deliverable"

    code_patch = payloads[2]
    assert code_patch["content_type"] == "text/x-diff"
    assert code_patch["summary"]["files_changed"] == ["src/foo.py"]

    test_report = payloads[3]
    assert test_report["summary"]["status"] == "passed"
    assert test_report["structured_output"]["output_preview"] == "1 passed"


def test_build_artifact_payloads_rejects_unsafe_code_file_path() -> None:
    output_snapshot = {
        "deliverables": [
            {
                "type": "code_file",
                "path": "../outside.py",
                "content": "print('unsafe')",
            }
        ]
    }

    payloads = build_artifact_payloads(task_id="task_1", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == ["json", "generic_result"]
    assert payloads[1]["metadata"]["warning"] == "invalid_code_file_deliverable"


def test_build_artifact_payloads_sanitizes_absolute_code_file_path() -> None:
    output_snapshot = {
        "deliverables": [
            {
                "type": "code_file",
                "path": "/tmp/hello_world.py",
                "language": "python",
                "content": "print('Hello, World!')\n",
            }
        ]
    }

    payloads = build_artifact_payloads(task_id="task_1", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == ["json", "code_file"]
    assert payloads[1]["uri"] == "workspace://generated/hello_world.py"
    assert payloads[1]["metadata"]["warning"] == "sanitized_code_file_path"


def test_build_artifact_payloads_infers_code_file_from_legacy_result_code() -> None:
    output_snapshot = {
        "status": "success",
        "result": {
            "code": "def is_hex_string(value):\n    return bool(value)\n",
            "language": "python",
            "stage": "planner",
        },
    }

    payloads = build_artifact_payloads(task_id="task_abc", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == ["json", "code_file"]
    code_file = payloads[1]
    assert code_file["uri"] == "workspace://generated/task_abc.py"
    assert code_file["summary"] == {
        "path": "generated/task_abc.py",
        "language": "python",
        "change_type": "created",
    }
    assert code_file["raw_content"]["content"].startswith("def is_hex_string")


def test_build_artifact_payloads_infers_code_file_from_nested_legacy_result_code() -> None:
    output_snapshot = {
        "status": "success",
        "result": {
            "summary": "generated",
            "result": {
                "code": "print('nested')\n",
                "language": "python",
            },
        },
    }

    payloads = build_artifact_payloads(task_id="task_nested", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == ["json", "code_file"]
    assert payloads[1]["uri"] == "workspace://generated/task_nested.py"
    assert payloads[1]["raw_content"]["content"] == "print('nested')\n"


def test_build_artifact_payloads_preserves_go_code_file_metadata() -> None:
    output_snapshot = {
        "status": "success",
        "result": {
            "deliverables": [
                {
                    "type": "code_file",
                    "path": "check_string_empty.go",
                    "language": "go",
                    "content": "package main\n",
                }
            ],
        },
    }

    payloads = build_artifact_payloads(task_id="task_go", run_id="run_1", output_snapshot=output_snapshot)

    assert [payload["artifact_type"] for payload in payloads] == ["json", "code_file"]
    assert payloads[1]["uri"] == "workspace://check_string_empty.go"
    assert payloads[1]["content_type"] == "text/x-go"
    assert payloads[1]["summary"]["language"] == "go"


def test_build_artifact_payloads_uses_markdown_document_contract_for_legacy_code() -> None:
    output_snapshot = {
        "status": "success",
        "summary": "Generated Go code",
        "result": {
            "code": "package main\n\nfunc IsEmpty(value string) bool {\n\treturn value == \"\"\n}\n",
            "language": "go",
        },
    }
    input_snapshot = {
        "deliverable_contract": {
            "expected_artifact_types": ["document"],
            "presentation_format": "markdown",
            "file_extension": ".md",
            "include_code_block": True,
            "require_file_level_artifact": False,
            "allow_primary_json_only": False,
        }
    }

    payloads = build_artifact_payloads(
        task_id="task_go_md",
        run_id="run_1",
        output_snapshot=output_snapshot,
        input_snapshot=input_snapshot,
    )

    assert [payload["artifact_type"] for payload in payloads] == ["json", "document"]
    assert payloads[1]["uri"] == "workspace://generated/task_go_md.md"
    assert payloads[1]["content_type"] == "text/markdown"
    assert payloads[1]["raw_content"]["content"].startswith("```go\npackage main")


def test_build_artifact_payloads_uses_document_contract_for_result_content() -> None:
    output_snapshot = {
        "status": "success",
        "summary": "Generated essay",
        "result": {
            "content": "我的母亲是一位平凡而伟大的女性。",
            "word_count": 800,
        },
    }
    input_snapshot = {
        "deliverable_contract": {
            "expected_artifact_types": ["document"],
            "presentation_format": "markdown",
            "file_extension": ".md",
            "include_code_block": False,
            "require_file_level_artifact": False,
            "allow_primary_json_only": False,
        }
    }

    payloads = build_artifact_payloads(
        task_id="task_essay",
        run_id="run_1",
        output_snapshot=output_snapshot,
        input_snapshot=input_snapshot,
    )

    assert [payload["artifact_type"] for payload in payloads] == ["json", "document"]
    assert payloads[1]["content_type"] == "text/markdown"
    assert payloads[1]["raw_content"]["content"] == "我的母亲是一位平凡而伟大的女性。"


def test_build_artifact_payloads_does_not_fake_missing_patch() -> None:
    output_snapshot = {
        "status": "success",
        "summary": "Generated explanation only",
        "result": {"code": "print('not a diff')\n", "language": "python"},
    }
    input_snapshot = {
        "deliverable_contract": {
            "expected_artifact_types": ["code_patch"],
            "presentation_format": None,
            "file_extension": None,
            "include_code_block": False,
            "require_file_level_artifact": False,
            "allow_primary_json_only": False,
        }
    }

    payloads = build_artifact_payloads(
        task_id="task_patch",
        run_id="run_1",
        output_snapshot=output_snapshot,
        input_snapshot=input_snapshot,
    )

    assert [payload["artifact_type"] for payload in payloads] == ["json"]


def test_extract_deliverables_ignores_non_list_values() -> None:
    assert extract_deliverables({"deliverables": {"type": "code_file"}}) == []
    assert extract_deliverables({"result": {"deliverables": "invalid"}}) == []
