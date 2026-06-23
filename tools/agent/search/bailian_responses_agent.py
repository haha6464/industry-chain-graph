
from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from tools.agent.common import PROJECT_ROOT, content_hash, now_iso

DEFAULT_BASE_URL = "https://llm-5h22uw9yblw6v1rz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_SEARCH_STRATEGY = "agent_max"


class BailianAgentError(RuntimeError):
    pass


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)


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
        raise BailianAgentError("Qwen response did not contain a JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BailianAgentError(f"Qwen response JSON parse failed: {exc}") from exc


def _build_prompt(industry_id: str, industry_name: str, target_depth: str) -> str:
    return f"""
你是一个面向证券研究场景的产业链图谱构建 Agent。请你必须联网搜索公开资料，为“{industry_name}”构建一个标清产业链图谱。

硬性要求：
1. 必须使用联网搜索和网页抽取能力，不要只依赖模型内部知识。
2. 不要抽取公司节点，不要输出公司列表，不要涉及股票代码、财务指标或个股信息。
3. 图谱只允许两类关系：contains 和 upstream_downstream。
4. contains 的方向是 父节点 -> 子节点；upstream_downstream 的方向是 上游节点 -> 下游节点。
5. 每个节点和每条关系都必须保留至少 1 个 URL 来源。
6. 尽量覆盖上游、中游、下游、支持环节，目标层级深度：{target_depth}。
7. 输出必须是严格 JSON，不要输出 Markdown、解释文字或代码块。

请返回如下 JSON 结构：
{{
  "industry": "{industry_name}",
  "version": "v0.1-agent-search",
  "schema_version": "standard_industry_graph_v0.2_agent",
  "generated_at": "",
  "scope": "标清产业链图谱；不包含公司节点、股票代码、财务指标。",
  "source_basis": [{{"name": "资料标题或机构名称", "url": "https://...", "note": "该来源支持的产业链判断"}}],
  "nodes": [{{
    "id": "{industry_id}_001",
    "name": "节点名称",
    "node_type": "产业链/产业链环节/细分环节/原材料/产品/渠道/应用场景",
    "tags": ["level_0", "root"],
    "industry": "{industry_name}",
    "level": 0,
    "chain_position": "root/upstream/midstream/downstream/support",
    "parent_id": "",
    "description": "一句话业务描述",
    "business_description": "一句话业务描述",
    "is_key_node": true,
    "chain_segment": "root/上游/中游/下游/支持",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}],
  "edges": [{{
    "source": "{industry_id}_001",
    "target": "{industry_id}_002",
    "relation_type": "contains",
    "relation_weight": 1.0,
    "description": "关系说明",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}]
}}
""".strip()


def call_bailian_search_agent(industry_id: str, industry_name: str, target_depth: str = "5-6 层") -> tuple[dict[str, Any], str]:
    _load_env()
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")
    if not api_key:
        raise BailianAgentError("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required for --mode search.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BailianAgentError("openai package is required for --mode search. Run .\\scripts\\setup-conda.ps1 to update the conda environment.") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", DEFAULT_BASE_URL),
    )
    response = client.responses.create(
        model=os.getenv("BAILIAN_MODEL", DEFAULT_MODEL),
        input=_build_prompt(industry_id, industry_name, target_depth),
        tools=[
            {"type": "web_search"},
            {"type": "web_extractor"},
            {"type": "code_interpreter"},
        ],
        extra_body={
            "enable_thinking": os.getenv("BAILIAN_ENABLE_THINKING", "true").lower() == "true",
            "search_options": {
                "forced_search": True,
                "search_strategy": os.getenv("BAILIAN_SEARCH_STRATEGY", DEFAULT_SEARCH_STRATEGY),
            },
        },
    )
    raw_text = _response_text(response)
    return _extract_json_object(raw_text), raw_text


def evidence_from_agent_graph(industry_id: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for source in graph.get("source_basis", []) or []:
        url = source.get("url")
        if url:
            seen[url] = {"title": source.get("name", ""), "snippet": source.get("note", "")}
    for item in list(graph.get("nodes", []) or []) + list(graph.get("edges", []) or []):
        for url in item.get("source_urls", []) or []:
            seen.setdefault(url, {"title": "", "snippet": item.get("description", "")})

    retrieved_at = now_iso()
    rows = []
    for index, (url, meta) in enumerate(seen.items(), start=1):
        snippet = meta.get("snippet", "")
        rows.append(
            {
                "evidence_id": f"{industry_id}_ev_{index:04d}",
                "url": url,
                "title": meta.get("title", ""),
                "published_at": "",
                "retrieved_at": retrieved_at,
                "content_type": "web_search_result",
                "content_hash": content_hash(url + snippet),
                "snippet": snippet,
                "status": "ok",
            }
        )
    return rows
