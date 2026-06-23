from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv

from tools.agent.common import PROJECT_ROOT, standardize_graph
from tools.agent.search.bailian_responses_agent import BailianAgentError

DEFAULT_BASE_URL = "https://llm-5h22uw9yblw6v1rz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_SEARCH_STRATEGY = "agent_max"


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
        raise BailianAgentError("Validation response did not contain a JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BailianAgentError(f"Validation response JSON parse failed: {exc}") from exc


def _compact_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "industry": graph.get("industry"),
        "schema_version": graph.get("schema_version"),
        "source_basis": graph.get("source_basis", []),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }


def _build_prompt(graph: dict[str, Any], deterministic_report: dict[str, Any]) -> str:
    payload = {
        "graph": _compact_graph(graph),
        "deterministic_validation": deterministic_report,
    }
    return """
你是一个产业链图谱校验与最小修正 Agent。你的任务不是重新生成整张图，而是在候选图谱基础上做严格校验，并只做必要的最小修改。

校验规则：
1. 不允许公司节点、公司列表、股票代码、财务指标、个股信息。
2. 每个节点和每条关系必须至少有 1 个 URL 来源。
3. 只允许 contains 和 upstream_downstream 两类关系。
4. contains 表示父节点 -> 子节点；upstream_downstream 表示上游 -> 下游。
5. 同一节点对只允许一种主关系。
6. 节点命名要避免明显同义重复。
7. 层级、chain_position、chain_segment 要符合产业链常识。
8. 如果能通过小修解决问题，请直接修改 graph；如果需要大改或证据不足，放入 review_items，不要臆造。

允许的最小修改：
- 合并明显重复或同义节点，并同步关系引用。
- 修正明显错误的层级、chain_position、chain_segment、parent_id。
- 修正明显反向的上下游关系。
- 删除明显违反规则的公司/股票/财务节点或关系。
- 补齐缺失但可由已有来源支持的 description、source_urls、confidence。
- 对无法确认的问题添加 review_items。

禁止：
- 大规模重写图谱。
- 在没有 URL 来源时新增节点或关系。
- 引入公司字段或公司节点。
- 输出 Markdown 或解释文字。

请返回严格 JSON：
{
  "validation_status": "pass/needs_review/fail",
  "summary": "一句话总结",
  "modified_graph": {完整 graph JSON，包含 nodes 和 edges},
  "modifications": [
    {"type": "merge_node/update_node/update_edge/delete_node/delete_edge", "target_id": "", "reason": "", "source_urls": []}
  ],
  "review_items": [
    {"severity": "warning/error", "item_id": "", "reason": "", "suggestion": ""}
  ]
}

输入如下：
""".strip() + "\n" + json.dumps(payload, ensure_ascii=False)


def _check_minimal_change(original: dict[str, Any], modified: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    original_nodes = original.get("nodes", [])
    modified_nodes = modified.get("nodes", [])
    original_edges = original.get("edges", [])
    modified_edges = modified.get("edges", [])
    node_delta = abs(len(modified_nodes) - len(original_nodes))
    edge_delta = abs(len(modified_edges) - len(original_edges))
    max_node_delta = max(5, int(len(original_nodes) * 0.15))
    max_edge_delta = max(10, int(len(original_edges) * 0.15))
    if node_delta > max_node_delta:
        issues.append({"severity": "error", "item_id": "nodes", "reason": "校验 Agent 修改节点数量过大，疑似重生成。", "suggestion": "人工复核 modified_graph。"})
    if edge_delta > max_edge_delta:
        issues.append({"severity": "error", "item_id": "edges", "reason": "校验 Agent 修改关系数量过大，疑似重生成。", "suggestion": "人工复核 modified_graph。"})
    for node in modified_nodes:
        if "company_list" in node or "公司列表" in node:
            issues.append({"severity": "error", "item_id": node.get("id", ""), "reason": "校验 Agent 引入了公司字段。", "suggestion": "删除公司字段。"})
    return issues


def validate_and_repair_with_bailian(
    graph: dict[str, Any],
    industry_id: str,
    deterministic_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    _load_env()
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")
    if not api_key:
        raise BailianAgentError("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required for semantic validation and repair.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BailianAgentError("openai package is required for semantic validation. Run .\\scripts\\setup-conda.ps1 to update the conda environment.") from exc

    standardized = standardize_graph(graph, industry_id)
    client = OpenAI(api_key=api_key, base_url=os.getenv("BAILIAN_BASE_URL", DEFAULT_BASE_URL))
    response = client.responses.create(
        model=os.getenv("BAILIAN_MODEL", DEFAULT_MODEL),
        input=_build_prompt(standardized, deterministic_report),
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
    result = _extract_json_object(raw_text)
    modified_graph = result.get("modified_graph") or standardized
    modified_graph = standardize_graph(modified_graph, industry_id)
    guardrail_items = _check_minimal_change(standardized, modified_graph)
    review_items = list(result.get("review_items", []) or []) + guardrail_items
    validation_status = result.get("validation_status", "needs_review")
    if any(item.get("severity") == "error" for item in review_items):
        validation_status = "fail"
    report = {
        "validation_status": validation_status,
        "summary": result.get("summary", ""),
        "modifications": result.get("modifications", []) or [],
        "review_items": review_items,
    }
    return modified_graph, report, raw_text
