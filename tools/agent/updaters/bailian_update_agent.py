from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tools.agent.common import PROJECT_ROOT
from tools.agent.bailian_client import BailianAgentError, call_bailian_responses, load_bailian_env


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = getattr(item, "content", None)
        if isinstance(item, dict):
            content = item.get("content")
        for part in content or []:
            text = getattr(part, "text", None)
            if isinstance(part, dict):
                text = part.get("text")
            if text:
                chunks.append(str(text))
    if chunks:
        return "\n".join(chunks)
    if hasattr(response, "model_dump_json"):
        return response.model_dump_json(indent=2)
    return str(response)


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise BailianAgentError("Update response did not contain a JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BailianAgentError(f"Update response JSON parse failed: {exc}") from exc


def _compact_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "industry": graph.get("industry"),
        "schema_version": graph.get("schema_version"),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }


def build_bailian_update_prompt(graph: dict[str, Any], recent_sources: list[dict[str, Any]], mode: str) -> str:
    payload = {
        "mode": mode,
        "existing_graph": _compact_graph(graph),
        "recent_source_samples": recent_sources[-20:],
    }
    return """
你是一个产业链图谱增量更新 Agent。请联网搜索公开资料，判断现有图谱是否需要小范围增量更新。

原则：
1. 没有足够新增证据就返回 no_change，不要为了更新而更新。
2. 不允许公司节点、公司列表、股票代码、财务指标、个股信息。
3. 只允许 contains 和 upstream_downstream 两类关系。
4. 新增或修改节点/关系必须有 URL 来源。
5. 只做增量 diff，不要重写整张图。
6. 删除要保守：优先 deprecate，不要直接删除。
7. 证据不足、争议较大或需要行业研究员判断的内容放入 review_items。

请返回严格 JSON：
{
  "status": "no_change/proposed/apply_ready/needs_review",
  "reason": "一句话说明",
  "checked_sources": [{"title": "", "url": "", "note": ""}],
  "add_nodes": [完整 node JSON],
  "add_edges": [完整 edge JSON],
  "modify_nodes": [{"id": "", "patch": {}, "reason": "", "source_urls": []}],
  "modify_edges": [{"id": "", "source": "", "target": "", "patch": {}, "reason": "", "source_urls": []}],
  "remove_or_deprecate": [{"id": "", "item_type": "node/edge", "reason": "", "source_urls": []}],
  "review_items": [{"severity": "warning/error", "item_id": "", "reason": "", "suggestion": ""}]
}

输入如下：
""".strip() + "\n" + json.dumps(payload, ensure_ascii=False)


def call_bailian_update_agent(graph: dict[str, Any], recent_sources: list[dict[str, Any]], mode: str, prompt_path: Path | None = None) -> tuple[dict[str, Any], str]:
    load_bailian_env()
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")
    if not api_key:
        raise BailianAgentError("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required for update agent.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BailianAgentError("openai package is required for update agent. Run .\\scripts\\setup-conda.ps1 to update the conda environment.") from exc

    prompt = build_bailian_update_prompt(graph, recent_sources, mode)
    if prompt_path:
        prompt_path.write_text(prompt, encoding="utf-8")
    response = call_bailian_responses(prompt, "增量更新")
    raw_text = _response_text(response)
    return _extract_json_object(raw_text), raw_text

