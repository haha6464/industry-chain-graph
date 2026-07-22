from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from tools.agent.common import PROJECT_ROOT, standardize_graph
from tools.agent.bailian_client import BailianAgentError, call_bailian_responses, load_bailian_env


REPAIRABLE_ERROR_CODES = {
    "node_missing_source",
    "company_field_present",
    "invalid_level",
    "edge_missing_source_node",
    "edge_missing_target_node",
    "invalid_relation_type",
    "edge_missing_source",
    "relation_conflict",
}

NODE_FORMAT_FIELDS = {
    "node_type",
    "tags",
    "industry",
    "level",
    "chain_position",
    "chain_segment",
    "parent_id",
    "is_key_node",
    "source_urls",
    "evidence_ids",
    "confidence",
    "updated_at",
}
EDGE_FORMAT_FIELDS = {
    "source",
    "target",
    "relation_type",
    "relation_weight",
    "source_urls",
    "evidence_ids",
    "confidence",
    "updated_at",
}
REMOVABLE_COMPANY_FIELDS = {"company_list", "公司列表", "stock_code", "股票代码", "financial_metrics", "财务指标"}


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
  "repairs": [
    {
      "action": "update_node/update_edge/delete_edge",
      "target_id": "节点或关系 ID；关系也可使用 source->target",
      "changes": {"只填写需要修改的字段": "新值"},
      "remove_fields": ["仅删除明确违规的公司、股票或财务字段"],
      "reason": "对应的硬规则错误"
    }
  ],
  "review_items": [
    {"severity": "warning/error", "item_id": "", "reason": "", "suggestion": ""}
  ]
}

输入如下：
""".strip() + "\n" + json.dumps(payload, ensure_ascii=False)


def repairable_hard_rule_errors(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors = [item for item in report.get("issues", []) if item.get("severity") == "error"]
    repairable = [item for item in errors if item.get("code") in REPAIRABLE_ERROR_CODES]
    unrepairable = [item for item in errors if item.get("code") not in REPAIRABLE_ERROR_CODES]
    return repairable, unrepairable


def _edge_matches(edge: dict[str, Any], target_id: str) -> bool:
    return target_id in {
        str(edge.get("id", "")),
        f"{edge.get('source', '')}->{edge.get('target', '')}",
    }


def _apply_repairs(graph: dict[str, Any], repairs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    modified = copy.deepcopy(graph)
    nodes = modified.get("nodes", [])
    edges = modified.get("edges", [])
    guardrail_items: list[dict[str, Any]] = []

    for repair in repairs:
        action = str(repair.get("action") or repair.get("type") or "").strip()
        target_id = str(repair.get("target_id", "")).strip()
        changes = repair.get("changes") if isinstance(repair.get("changes"), dict) else {}

        if action == "update_node":
            matches = [node for node in nodes if str(node.get("id", "")) == target_id]
            if len(matches) != 1:
                guardrail_items.append({
                    "severity": "error",
                    "item_id": target_id,
                    "reason": "节点格式补丁无法唯一定位原节点。",
                    "suggestion": "人工处理重复或缺失的节点 ID。",
                })
                continue
            node = matches[0]
            for key, value in changes.items():
                if key in NODE_FORMAT_FIELDS:
                    node[key] = value
            for key in repair.get("remove_fields", []) or []:
                if key in REMOVABLE_COMPANY_FIELDS:
                    node.pop(key, None)
            continue

        matching_edges = [edge for edge in edges if _edge_matches(edge, target_id)]
        if action == "update_edge":
            if len(matching_edges) != 1:
                guardrail_items.append({
                    "severity": "error",
                    "item_id": target_id,
                    "reason": "关系格式补丁无法唯一定位原关系。",
                    "suggestion": "人工处理重复或缺失的关系 ID。",
                })
                continue
            for key, value in changes.items():
                if key in EDGE_FORMAT_FIELDS:
                    matching_edges[0][key] = value
            continue

        if action == "delete_edge":
            if not matching_edges:
                guardrail_items.append({
                    "severity": "warning",
                    "item_id": target_id,
                    "reason": "待删除关系未在原图中找到。",
                    "suggestion": "核对格式修复返回的关系 ID。",
                })
                continue
            edges[:] = [edge for edge in edges if not _edge_matches(edge, target_id)]
            continue

        guardrail_items.append({
            "severity": "error",
            "item_id": target_id,
            "reason": f"格式修复返回了不允许的操作：{action or 'empty'}。",
            "suggestion": "只允许 update_node、update_edge 或 delete_edge。",
        })

    return modified, guardrail_items

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
    response = call_bailian_responses(
        prompt,
        "格式修复",
        use_search_tools=False,
        enable_thinking=False,
    )
    raw_text = _response_text(response)
    result = _extract_json_object(raw_text)
    repairs = result.get("repairs") or []
    if not isinstance(repairs, list):
        raise BailianAgentError("格式修复响应中的 repairs 必须是数组。")
    modified_graph, patch_guardrail_items = _apply_repairs(standardized, repairs)
    modified_graph = standardize_graph(modified_graph, industry_id)
    guardrail_items = patch_guardrail_items + _check_minimal_change(standardized, modified_graph)
    review_items = list(result.get("review_items", []) or []) + guardrail_items
    validation_status = result.get("validation_status", "needs_review")
    if any(item.get("severity") == "error" for item in review_items):
        validation_status = "fail"
    report = {
        "validation_status": validation_status,
        "summary": result.get("summary", ""),
        "modifications": repairs,
        "review_items": review_items,
    }
    return modified_graph, report, raw_text


