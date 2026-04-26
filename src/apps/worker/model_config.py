from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_CONFIG_PATH = ROOT_DIR / "model_config.json"

_CACHE: dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "config": None,
}


def _config_path() -> Path:
    raw_path = os.getenv("TASK_FORGE_MODEL_CONFIG", "").strip()
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_MODEL_CONFIG_PATH


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def load_model_config() -> dict[str, Any]:
    path = _config_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        return {"enabled": False, "path": str(path)}

    if (
        _CACHE["config"] is not None
        and _CACHE["path"] == str(path)
        and _CACHE["mtime_ns"] == mtime_ns
    ):
        return deepcopy(_CACHE["config"])

    raw_config = json.loads(path.read_text(encoding="utf-8"))
    config = _expand_env(raw_config)
    config.setdefault("enabled", False)
    config["path"] = str(path)

    _CACHE["path"] = str(path)
    _CACHE["mtime_ns"] = mtime_ns
    _CACHE["config"] = deepcopy(config)
    return deepcopy(config)


def resolve_model_request_config(role_name: str) -> dict[str, Any]:
    config = load_model_config()
    request = deepcopy(config.get("request") or {})
    defaults = deepcopy(config.get("defaults") or {})
    role_overrides = deepcopy((config.get("agents") or {}).get(role_name) or {})

    headers = dict(request.get("headers") or {})
    headers.update(role_overrides.pop("headers", {}) or {})

    extra_body = dict(defaults.get("extra_body") or {})
    extra_body.update(role_overrides.pop("extra_body", {}) or {})

    resolved = {
        "enabled": bool(config.get("enabled")) and role_overrides.get("enabled", True),
        "path": config.get("path"),
        "request_format": request.get("format", "openai_chat_completions"),
        "url": request.get("url", ""),
        "timeout_seconds": float(request.get("timeout_seconds", 120)),
        "headers": headers,
        "model": role_overrides.get("model", defaults.get("model", "")),
        "temperature": role_overrides.get("temperature", defaults.get("temperature", 0.2)),
        "max_tokens": role_overrides.get("max_tokens", defaults.get("max_tokens", 1024)),
        "system_prompt": role_overrides.get(
            "system_prompt",
            defaults.get("system_prompt", ""),
        ),
        "extra_body": extra_body,
    }
    resolved["metadata"] = {
        "config_path": config.get("path"),
        "role_name": role_name,
    }
    return resolved
