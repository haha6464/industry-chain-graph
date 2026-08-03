from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from tools.agent.company_attachments import (
    CANDIDATE_CSV_PATH,
    COMPANY_ATTACHMENT_SCHEMA_VERSION,
    TAXONOMY_COLUMNS,
    build_deterministic_taxonomy_matches,
    candidate_index,
    file_sha256,
    filter_domestic_listed_companies,
    graph_fingerprint,
    is_ancestor,
    load_candidate_companies,
    select_companies_by_scope,
)


def validate_company_attachments(payload: dict[str, Any], graph: dict[str, Any], industry_id: str) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def issue(code: str, message: str, item_id: str = "") -> None:
        issues.append({"severity": "error", "code": code, "message": message, "item_id": item_id})

    if payload.get("schema_version") != COMPANY_ATTACHMENT_SCHEMA_VERSION:
        issue("invalid_schema_version", "公司挂载 schema_version 不正确。")
    if payload.get("industry_id") != industry_id:
        issue("industry_mismatch", "公司挂载行业与目标行业不一致。")
    if payload.get("graph_fingerprint") != graph_fingerprint(graph):
        issue("graph_fingerprint_mismatch", "公司挂载对应的正式图谱已变化。")
    source = payload.get("candidate_source") or {}
    if source.get("sha256") != file_sha256(CANDIDATE_CSV_PATH):
        issue("candidate_source_mismatch", "候选公司 CSV 哈希不一致。")
    rules = (payload.get("scope") or {}).get("rules", []) or []
    if not rules:
        issue("scope_missing", "缺少可追溯的候选公司范围规则。")
    for rule in rules:
        if rule.get("column") not in TAXONOMY_COLUMNS or not rule.get("values"):
            issue("invalid_scope_rule", "范围规则必须使用有效申万分类字段及非空精确值。")

    candidates = candidate_index(load_candidate_companies())
    companies = payload.get("companies", []) or []
    company_ids = [str(item.get("company_id", "")) for item in companies]
    for identifier, count in Counter(company_ids).items():
        if not identifier or count > 1:
            issue("duplicate_company_id", "公司主数据 company_id 缺失或重复。", identifier)
    for item in companies:
        identifier = str(item.get("company_id", ""))
        source_item = candidates.get(identifier)
        if source_item is None:
            issue("unknown_company", "公司不在候选公司 CSV 中。", identifier)
        elif any(item.get(key) != source_item.get(key) for key in ("comcode", "name", "short_name", "is_listed", "is_abroad_listed", "sw_industry")):
            issue("company_identity_mismatch", "公司主数据与候选 CSV 不一致。", identifier)

    node_by_id = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    flow_boundary_l1_ids: set[str] = set()
    for edge in graph.get("edges", []):
        if edge.get("relation_type") != "upstream_downstream":
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        source_level = int(node_by_id.get(source, {}).get("level", -1))
        target_level = int(node_by_id.get(target, {}).get("level", -1))
        if source_level == 0 and target_level == 1:
            flow_boundary_l1_ids.add(target)
        elif source_level == 1 and target_level == 0:
            flow_boundary_l1_ids.add(source)
    parents: dict[str, str] = {}
    for node_id, node in node_by_id.items():
        parent_id = str(node.get("parent_id") or "")
        if node_id in flow_boundary_l1_ids and int(node_by_id.get(parent_id, {}).get("level", -1)) == 0:
            parent_id = ""
        parents[node_id] = parent_id
    for edge in graph.get("edges", []):
        if edge.get("relation_type") == "contains" and edge.get("source") and edge.get("target"):
            target = str(edge["target"])
            if not parents.get(target):
                parents[target] = str(edge["source"])
    pairs: list[tuple[str, str]] = []
    attached_nodes: dict[str, list[str]] = defaultdict(list)
    for attachment in payload.get("attachments", []) or []:
        identifier = str(attachment.get("company_id", ""))
        node_id = str(attachment.get("node_id", ""))
        pairs.append((identifier, node_id))
        attached_nodes[identifier].append(node_id)
        if identifier not in company_ids:
            issue("attachment_unknown_company", "挂载引用了不存在的公司主数据。", identifier)
        node = node_by_id.get(node_id)
        if node is None:
            issue("attachment_unknown_node", "挂载引用了不存在的产业链节点。", node_id)
        elif int(node.get("level", 0)) == 0:
            issue("root_direct_attachment", "公司不得直接挂载到 L0 根节点。", node_id)
        if not str(attachment.get("reason", "")).strip():
            issue("attachment_reason_missing", "公司挂载缺少匹配原因。", f"{identifier}->{node_id}")
        try:
            confidence = float(attachment.get("confidence"))
            if not 0.0 <= confidence <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            issue("attachment_confidence_invalid", "公司挂载置信度必须在 0 到 1 之间。", f"{identifier}->{node_id}")
    for pair, count in Counter(pairs).items():
        if count > 1:
            issue("duplicate_attachment", "同一公司和节点存在重复挂载。", f"{pair[0]}->{pair[1]}")
    for identifier, node_ids in attached_nodes.items():
        for node_id in node_ids:
            if any(other != node_id and is_ancestor(node_id, other, parents) for other in node_ids):
                issue("ancestor_descendant_attachment", "同一公司同时挂载了祖先和后代节点。", identifier)
                break
    selected_candidates = select_companies_by_scope(
        filter_domestic_listed_companies(list(candidates.values())), payload.get("scope") or {}
    )
    required_exact_matches = build_deterministic_taxonomy_matches(graph, selected_candidates)
    for result in required_exact_matches:
        identifier = str(result.get("company_id", ""))
        actual_node_ids = attached_nodes.get(identifier, [])
        for expected in result.get("matched_nodes", []) or []:
            expected_node_id = str(expected.get("node_id", ""))
            if not any(
                actual == expected_node_id or is_ancestor(expected_node_id, actual, parents)
                for actual in actual_node_ids
            ):
                issue(
                    "obvious_taxonomy_match_missing",
                    "申万分类与产业节点精确对应，但公司未挂到该节点或其更深后代节点。",
                    f"{identifier}->{expected_node_id}",
                )
    error_count = len(issues)
    return {
        "industry_id": industry_id,
        "attachment_count": len(payload.get("attachments", []) or []),
        "company_count": len(companies),
        "error_count": error_count,
        "warning_count": 0,
        "status": "pass" if error_count == 0 else "fail",
        "issues": issues,
    }


def write_company_attachment_report(report: dict[str, Any]) -> str:
    lines = [
        "# 公司节点挂载硬规则校验报告",
        "",
        f"- 状态：{report.get('status')}",
        f"- 公司数：{report.get('company_count', 0)}",
        f"- 挂载数：{report.get('attachment_count', 0)}",
        f"- error：{report.get('error_count', 0)}",
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
