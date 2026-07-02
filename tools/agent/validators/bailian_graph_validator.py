from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tools.agent.common import PROJECT_ROOT, standardize_graph
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


def build_bailian_validation_prompt(graph: dict[str, Any], deterministic_report: dict[str, Any]) -> str:
    error_issues = [item for item in deterministic_report.get("issues", []) if item.get("severity") == "error"]
    payload = {
        "graph": _compact_graph(graph),
        "hard_rule_errors": error_issues,
    }
    return """
你是产业链图谱格式修复 Agent。请只根据硬规则错误修复 graph 的工程格式问题，不要评价或重构产业链分类质量。

只允许处理这些问题：
1. 缺失必填字段、字段类型错误、非法 relation_type。
2. contains / upstream_downstream 关系方向或引用节点不存在导致的格式错误。
3. 同一 source-target 存在多种主关系的冲突。
4. 节点或关系缺少 source_urls 时，只能从同节点、同关系两端节点或 source_basis 中已有 URL 补齐；没有依据则放入 review_items。
5. 公司字段、股票代码、财务指标等明显违反当前数据格式的内容。

禁止：
- 不要因为产业链质量、覆盖广度、层级粒度去新增或删除节点。
- 不要大规模重写图谱。
- 不要引入新的产业链判断。
- 不要联网补资料。
- 不要输出 Markdown 或解释文字。

请返回严格 JSON：
{
  "validation_status": "pass/needs_review/fail",
  "summary": "一句话说明修复了哪些格式问题",
  "modified_graph": {完整 graph JSON，包含 nodes 和 edges},
  "modifications": [
    {"type": "update_node/update_edge/delete_node/delete_edge", "target_id": "", "reason": "", "source_urls": []}
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
    prompt_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    load_bailian_env()
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")
    if not api_key:
        raise BailianAgentError("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required for format repair.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BailianAgentError("openai package is required for format repair. Run .\\scripts\\setup-conda.ps1 to update the conda environment.") from exc

    standardized = standardize_graph(graph, industry_id)
    prompt = build_bailian_validation_prompt(standardized, deterministic_report)
    if prompt_path:
        prompt_path.write_text(prompt, encoding="utf-8")
    response = call_bailian_responses(prompt, "格式修复", use_search_tools=False)
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


