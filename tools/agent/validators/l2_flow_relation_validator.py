from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlparse

from tools.agent.l2_flow_relations import (
    L1_L2_FLOW_PROJECTION_LAYER,
    L2_FLOW_RELATION_LAYER,
    L2_FLOW_SCHEMA_VERSION,
    PAIR_VERDICTS,
    build_l1_l2_projected_edges,
    graph_fingerprint,
    is_allowed_l2_flow_direction,
)


MIN_CONFIDENCE = 0.5


def validate_l2_flow_relations(payload: dict[str, Any], graph: dict[str, Any], industry_id: str) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def issue(severity: str, code: str, message: str, item_id: str = "") -> None:
        issues.append({"severity": severity, "code": code, "message": message, "item_id": item_id})

    if payload.get("schema_version") != L2_FLOW_SCHEMA_VERSION:
        issue("error", "invalid_schema_version", "L2 上下游关系 schema_version 不正确。")
    if payload.get("industry_id") != industry_id:
        issue("error", "industry_mismatch", "L2 上下游关系行业与目标行业不一致。")
    if payload.get("graph_fingerprint") != graph_fingerprint(graph):
        issue("error", "graph_fingerprint_mismatch", "L2 上下游关系对应的正式图谱已变化。")

    generation_config = payload.get("generation_config") or {}
    if generation_config.get("decision_mode") != "independent_pair_tri_state":
        issue("error", "invalid_decision_mode", "L2 建边必须使用独立节点对三态判定。")
    if generation_config.get("web_search") is not False or generation_config.get("thinking") is not False:
        issue("error", "invalid_generation_mode", "L2 建边必须关闭联网搜索和思考模式。")
    if generation_config.get("tools") not in ([], None):
        issue("error", "tools_enabled", "L2 建边不得启用工具。")
    try:
        temperature = float(generation_config.get("temperature"))
        if not 0.0 <= temperature <= 0.3:
            issue("error", "temperature_too_high", "L2 节点对判定 temperature 必须在 0 到 0.3 之间。")
    except (TypeError, ValueError):
        issue("error", "temperature_invalid", "L2 节点对判定缺少有效 temperature。")

    node_by_id = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    l2_ids = {node_id for node_id, node in node_by_id.items() if int(node.get("level", -1)) == 2}
    evaluated_ids = [str(node_id) for node_id in payload.get("evaluated_node_ids", []) or []]
    for node_id, count in Counter(evaluated_ids).items():
        if count > 1:
            issue("error", "duplicate_evaluated_node", "L2 节点评估目录记录重复。", node_id)
        if node_id not in l2_ids:
            issue("error", "evaluated_node_not_l2", "评估目录引用了非 L2 节点。", node_id)
    for node_id in sorted(l2_ids - set(evaluated_ids)):
        issue("error", "l2_node_not_cataloged", "L2 节点未进入候选召回目录。", node_id)

    summary = payload.get("candidate_summary") or {}
    decisions = payload.get("pair_decisions") or []
    if not isinstance(decisions, list):
        decisions = []
        issue("error", "pair_decisions_invalid", "pair_decisions 必须是数组。")
    try:
        candidate_pair_count = int(summary.get("candidate_pair_count"))
    except (TypeError, ValueError):
        candidate_pair_count = -1
        issue("error", "candidate_pair_count_invalid", "候选节点对数量无效。")
    if candidate_pair_count != len(decisions):
        issue("error", "pair_decision_incomplete", "每个候选节点对必须恰好有一个判定结果。")
    if summary.get("pair_decision_count") != len(decisions):
        issue("error", "pair_decision_summary_mismatch", "pair 判定汇总数量与明细不一致。")

    decision_by_pair_id: dict[str, dict[str, Any]] = {}
    expected_directions: dict[str, tuple[str, str]] = {}
    audit_positive_count = 0
    for decision in decisions:
        pair_identifier = str(decision.get("pair_id", ""))
        node_a = str(decision.get("node_a_id", ""))
        node_b = str(decision.get("node_b_id", ""))
        verdict = str(decision.get("verdict", "")).upper()
        if not pair_identifier:
            issue("error", "pair_id_missing", "节点对判定缺少 pair_id。")
            continue
        if pair_identifier in decision_by_pair_id:
            issue("error", "duplicate_pair_decision", "同一 pair_id 出现多个判定。", pair_identifier)
            continue
        decision_by_pair_id[pair_identifier] = decision
        if verdict not in PAIR_VERDICTS:
            issue("error", "invalid_pair_verdict", "pair 判定只允许 A_TO_B、B_TO_A 或 NO。", pair_identifier)
        if not node_a or not node_b or node_a == node_b:
            issue("error", "invalid_pair_endpoints", "候选节点对两端必须是不同节点。", pair_identifier)
        elif node_a not in l2_ids or node_b not in l2_ids:
            issue("error", "pair_endpoint_not_l2", "候选节点对两端必须都是 level=2。", pair_identifier)
        else:
            branch_a = str(node_by_id[node_a].get("parent_id") or "")
            branch_b = str(node_by_id[node_b].get("parent_id") or "")
            if not branch_a or branch_a == branch_b:
                issue("error", "same_branch_pair", "L2 节点对必须来自不同 L1 分支。", pair_identifier)
        if verdict == "A_TO_B":
            expected_directions[pair_identifier] = (node_a, node_b)
        elif verdict == "B_TO_A":
            expected_directions[pair_identifier] = (node_b, node_a)
        if verdict in {"A_TO_B", "B_TO_A"}:
            source, target = expected_directions[pair_identifier]
            if not is_allowed_l2_flow_direction(graph, source, target):
                issue(
                    "error",
                    "branch_role_policy_violation",
                    "L2 上下游关系只允许上游分支→隶属分支或隶属分支→下游分支。",
                    pair_identifier,
                )
        if (
            "deterministic_negative_audit" in (decision.get("selection_reasons") or [])
            and verdict in {"A_TO_B", "B_TO_A"}
        ):
            audit_positive_count += 1

    main_pairs = {
        frozenset((str(edge.get("source", "")), str(edge.get("target", ""))))
        for edge in graph.get("edges", [])
        if edge.get("source") and edge.get("target")
    }
    edge_by_pair_id: dict[str, dict[str, Any]] = {}
    directed_pairs: list[tuple[str, str]] = []
    for edge in payload.get("edges", []) or []:
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        identifier = str(edge.get("id") or f"{source}->{target}")
        decision_pair_id = str(edge.get("decision_pair_id", ""))
        directed_pairs.append((source, target))
        if not decision_pair_id:
            issue("error", "decision_pair_id_missing", "脚本生成的关系缺少对应 pair 判定 ID。", identifier)
        elif decision_pair_id in edge_by_pair_id:
            issue("error", "duplicate_edge_for_pair", "同一 pair 判定生成了多条关系。", decision_pair_id)
        else:
            edge_by_pair_id[decision_pair_id] = edge
        expected_direction = expected_directions.get(decision_pair_id)
        if expected_direction is None:
            issue("error", "edge_without_positive_decision", "关系没有对应的正向 pair 判定。", identifier)
        elif (source, target) != expected_direction:
            issue("error", "edge_direction_mismatch", "关系方向与 pair 三态判定不一致。", identifier)
        if edge.get("relation_type") != "upstream_downstream":
            issue("error", "invalid_relation_type", "L2 横向关系只允许 upstream_downstream。", identifier)
        if edge.get("relation_layer") != L2_FLOW_RELATION_LAYER:
            issue("error", "invalid_relation_layer", "L2 横向关系缺少 l2_flow 层标记。", identifier)
        if source == target:
            issue("error", "self_loop", "L2 上下游关系不得自环。", identifier)
        if source not in node_by_id or target not in node_by_id:
            issue("error", "unknown_endpoint", "L2 上下游关系引用了不存在的节点。", identifier)
        elif source not in l2_ids or target not in l2_ids:
            issue("error", "endpoint_not_l2", "L2 上下游关系两端必须都是 level=2。", identifier)
        elif not is_allowed_l2_flow_direction(graph, source, target):
            issue(
                "error",
                "branch_role_policy_violation",
                "L2 上下游关系只允许上游分支→隶属分支或隶属分支→下游分支。",
                identifier,
            )
        if frozenset((source, target)) in main_pairs:
            issue("error", "main_relation_conflict", "该节点对已存在主图关系，不能重复建立 L2 横向关系。", identifier)
        if not str(edge.get("description", "")).strip():
            issue("error", "description_missing", "L2 上下游关系缺少直接关系说明。", identifier)
        urls = edge.get("source_urls", []) or []
        if not urls:
            issue("error", "source_url_missing", "L2 上下游关系缺少来源 URL。", identifier)
        elif any(urlparse(str(url)).scheme not in {"http", "https"} for url in urls):
            issue("error", "source_url_invalid", "L2 上下游关系包含无效 URL。", identifier)
        if not edge.get("evidence_ids"):
            issue("warning", "evidence_id_missing", "L2 上下游关系未关联证据 ID。", identifier)
        try:
            confidence = float(edge.get("confidence"))
            if not 0.0 <= confidence <= 1.0:
                raise ValueError
            if confidence < MIN_CONFIDENCE:
                issue("error", "confidence_below_minimum", "L2 上下游关系置信度低于 0.5。", identifier)
        except (TypeError, ValueError):
            issue("error", "confidence_invalid", "L2 上下游关系置信度必须在 0 到 1 之间。", identifier)

    for pair_identifier in sorted(expected_directions.keys() - edge_by_pair_id.keys()):
        issue("error", "positive_decision_not_materialized", "正向 pair 判定未被固定脚本生成关系。", pair_identifier)
    for pair, count in Counter(directed_pairs).items():
        if count > 1:
            issue("error", "duplicate_relation", "同一方向的 L2 上下游关系重复。", f"{pair[0]}->{pair[1]}")
        if pair[0] != pair[1] and (pair[1], pair[0]) in directed_pairs:
            issue("error", "reverse_relation_conflict", "同一 L2 节点对出现相反方向。", f"{pair[0]}<->{pair[1]}")

    projected_edges = payload.get("projected_edges") or []
    if not isinstance(projected_edges, list):
        projected_edges = []
        issue("error", "projected_edges_invalid", "L1-L2 投影关系必须是数组。")
    expected_projected = build_l1_l2_projected_edges(graph, payload.get("edges", []) or [])
    expected_projected_by_id = {str(edge["id"]): edge for edge in expected_projected}
    actual_projected_by_id: dict[str, dict[str, Any]] = {}
    comparable_fields = (
        "source",
        "target",
        "relation_type",
        "relation_layer",
        "relation_weight",
        "source_urls",
        "evidence_ids",
        "confidence",
        "evidence_basis",
        "projection_roles",
        "projected_from_count",
        "projected_from_edge_ids",
    )
    for edge in projected_edges:
        identifier = str(edge.get("id", ""))
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if not identifier:
            issue("error", "projected_edge_id_missing", "L1-L2 投影关系缺少 ID。")
            continue
        if identifier in actual_projected_by_id:
            issue("error", "duplicate_projected_relation", "同一方向的 L1-L2 投影关系重复。", identifier)
            continue
        actual_projected_by_id[identifier] = edge
        if edge.get("relation_layer") != L1_L2_FLOW_PROJECTION_LAYER:
            issue("error", "invalid_projected_relation_layer", "L1-L2 投影关系层标记不正确。", identifier)
        if source not in node_by_id or target not in node_by_id:
            issue("error", "projected_unknown_endpoint", "L1-L2 投影关系引用了不存在的节点。", identifier)
        elif {int(node_by_id[source].get("level", -1)), int(node_by_id[target].get("level", -1))} != {1, 2}:
            issue("error", "projected_endpoint_levels_invalid", "投影关系必须连接一个 L1 节点和一个 L2 节点。", identifier)
        expected = expected_projected_by_id.get(identifier)
        if expected is None:
            issue("error", "projected_relation_without_l2_basis", "L1-L2 投影关系没有对应的 L2 关系依据。", identifier)
            continue
        for field in comparable_fields:
            if edge.get(field) != expected.get(field):
                issue("error", "projected_attribute_mismatch", f"L1-L2 投影关系字段 {field} 与 L2 来源边不一致。", identifier)
        if not str(edge.get("description", "")).strip():
            issue("error", "projected_description_missing", "L1-L2 投影关系缺少说明。", identifier)
    for identifier in sorted(expected_projected_by_id.keys() - actual_projected_by_id.keys()):
        issue("error", "l1_l2_projection_missing", "L2 上下游关系未生成完整的 L1-L2 交叉投影。", identifier)

    if audit_positive_count > 0:
        issue(
            "warning",
            "candidate_recall_audit_positive",
            f"负样本审计发现 {audit_positive_count} 个正向关系，建议提高每节点候选数。",
        )
    if not payload.get("edges"):
        issue("warning", "no_l2_flow_relations", "候选节点对均未形成可靠上下游关系，建议人工检查。")

    error_count = sum(item["severity"] == "error" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    return {
        "industry_id": industry_id,
        "l2_node_count": len(l2_ids),
        "evaluated_node_count": len(set(evaluated_ids)),
        "candidate_pair_count": max(candidate_pair_count, 0),
        "pair_decision_count": len(decisions),
        "cache_hit_count": int(summary.get("cache_hit_count", 0) or 0),
        "model_decision_count": int(summary.get("model_decision_count", 0) or 0),
        "relation_count": len(payload.get("edges", []) or []),
        "projected_relation_count": len(projected_edges),
        "error_count": error_count,
        "warning_count": warning_count,
        "status": "pass" if error_count == 0 else "fail",
        "issues": issues,
    }


def write_l2_flow_validation_report(report: dict[str, Any]) -> str:
    lines = [
        "# L2 上下游关系硬规则校验报告",
        "",
        f"- 状态：{report.get('status')}",
        f"- L2 节点数：{report.get('l2_node_count', 0)}",
        f"- 候选节点对：{report.get('candidate_pair_count', 0)}",
        f"- pair 判定数：{report.get('pair_decision_count', 0)}",
        f"- 缓存命中：{report.get('cache_hit_count', 0)}",
        f"- 模型判定：{report.get('model_decision_count', 0)}",
        f"- 发布关系数：{report.get('relation_count', 0)}",
        f"- L1-L2 投影关系数：{report.get('projected_relation_count', 0)}",
        f"- error：{report.get('error_count', 0)}",
        f"- warning：{report.get('warning_count', 0)}",
        "",
        "## 问题列表",
        "",
    ]
    if not report.get("issues"):
        lines.append("未发现阻断性问题。")
    else:
        for item in report["issues"]:
            lines.append(f"- [{item['severity']}] {item['code']} {item.get('item_id', '')}：{item['message']}")
    return "\n".join(lines) + "\n"
