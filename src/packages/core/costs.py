from __future__ import annotations

PROMPT_COST_PER_1K = 0.001
COMPLETION_COST_PER_1K = 0.002


def estimate_cost(token_usage: dict | None) -> float:
    usage = token_usage or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cost = (prompt_tokens / 1000) * PROMPT_COST_PER_1K
    cost += (completion_tokens / 1000) * COMPLETION_COST_PER_1K
    return round(cost, 6)
