from src.packages.core.error_classification import classify_run_error, classify_task_error


def test_classify_run_error_detects_timeout() -> None:
    assert (
        classify_run_error(
            run_status="failed",
            error_message="subprocess timed out after 30 seconds",
        )
        == "timeout"
    )


def test_classify_run_error_detects_validation_error() -> None:
    assert (
        classify_run_error(
            run_status="failed",
            error_message="input_payload.text must be a non-empty string",
        )
        == "validation_error"
    )


def test_classify_task_error_detects_routing_error_from_review_context() -> None:
    assert (
        classify_task_error(
            task_status="needs_review",
            review_reason="No eligible agent role found for task_type=no_match",
        )
        == "routing_error"
    )


def test_classify_task_error_detects_dependency_blocked() -> None:
    assert (
        classify_task_error(
            task_status="blocked",
            dependency_ids=["task_upstream"],
        )
        == "dependency_blocked"
    )


def test_classify_run_error_detects_external_tool_error() -> None:
    assert (
        classify_run_error(
            run_status="failed",
            error_message="command failed with exit code 1",
            logs=["subprocess execution failed"],
        )
        == "external_tool_error"
    )


def test_classify_run_error_falls_back_to_execution_error() -> None:
    assert (
        classify_run_error(
            run_status="failed",
            error_message="unexpected boom",
        )
        == "execution_error"
    )
