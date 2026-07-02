from __future__ import annotations

import os
import time
from typing import Any

from dotenv import load_dotenv

from tools.agent.common import PROJECT_ROOT

DEFAULT_BASE_URL = "https://llm-5h22uw9yblw6v1rz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_SEARCH_STRATEGY = "agent_max"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_SLEEP_SECONDS = 3


class BailianAgentError(RuntimeError):
    pass


def load_bailian_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def bailian_tools() -> list[dict[str, str]]:
    tools = [
        {"type": "web_search"},
        {"type": "web_extractor"},
    ]
    if _env_bool("BAILIAN_ENABLE_CODE_INTERPRETER", False):
        tools.append({"type": "code_interpreter"})
    return tools


def call_bailian_responses(prompt: str, purpose: str, use_search_tools: bool = True) -> Any:
    load_bailian_env()
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")
    if not api_key:
        raise BailianAgentError("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required for Bailian agent calls.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BailianAgentError("openai package is required. Run .\\scripts\\setup-conda.ps1 to update the conda environment.") from exc

    timeout_seconds = _env_int("BAILIAN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    max_retries = _env_int("BAILIAN_MAX_RETRIES", DEFAULT_MAX_RETRIES)
    retry_sleep_seconds = _env_int("BAILIAN_RETRY_SLEEP_SECONDS", DEFAULT_RETRY_SLEEP_SECONDS)
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", DEFAULT_BASE_URL),
        timeout=timeout_seconds,
        max_retries=0,
    )
    tools = bailian_tools() if use_search_tools else []
    extra_body: dict[str, Any] = {"enable_thinking": _env_bool("BAILIAN_ENABLE_THINKING", True)}
    if use_search_tools:
        extra_body["search_options"] = {
            "forced_search": True,
            "search_strategy": os.getenv("BAILIAN_SEARCH_STRATEGY", DEFAULT_SEARCH_STRATEGY),
        }
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"[agent] 百炼{purpose}第 {attempt + 1} 次重试。", flush=True)
            time.sleep(retry_sleep_seconds)
        try:
            tool_summary = ",".join(tool["type"] for tool in tools) or "none"
            print(
                f"[agent] 调用百炼{purpose}：model={os.getenv('BAILIAN_MODEL', DEFAULT_MODEL)}, "
                f"strategy={os.getenv('BAILIAN_SEARCH_STRATEGY', DEFAULT_SEARCH_STRATEGY)}, "
                f"timeout={timeout_seconds}s, tools={tool_summary}。",
                flush=True,
            )
            request_kwargs: dict[str, Any] = {
                "model": os.getenv("BAILIAN_MODEL", DEFAULT_MODEL),
                "input": prompt,
                "extra_body": extra_body,
            }
            if tools:
                request_kwargs["tools"] = tools
            return client.responses.create(**request_kwargs)
        except Exception as exc:  # openai wraps httpx/httpcore errors by version.
            last_error = exc
            print(f"[agent] 百炼{purpose}调用失败：{type(exc).__name__}: {exc}", flush=True)

    raise BailianAgentError(
        f"百炼{purpose}调用失败，已重试 {max_retries} 次。"
        "可尝试降低 BAILIAN_SEARCH_STRATEGY、关闭 BAILIAN_ENABLE_THINKING、"
        "或调小目标节点数后重跑。"
    ) from last_error
