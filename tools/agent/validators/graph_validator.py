from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from tools.agent.common import load_graph, standardize_graph, write_json

ALLOWED_RELATIONS = {"contains", "upstream_downstream"}
MIN_CONFIDENCE = 0.5


def _norm_name(name: str) -> str:
    return "".join(name.lower().split())


def validate_graph(graph: dict[str, Any], industry_id: str) -> dict[str, Any]:
    graph = standardize_graph(graph, industry_id)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_by_id = {node["id"]: node for node in nodes}
    issues: list[dict[str, Any]] = []

    def issue(severity: str, code: str, message: str, item_id: str = "") -> None:
        issues.append({"severity": severity, "code": code, "message": message, "item_id": item_id})

    id_counts = Counter(node["id"] for node in nodes)
    for node_id, count in id_counts.items():
        if count > 1:
            issue("error", "duplicate_node_id", f"节点 ID 重复 {count} 次。", node_id)

    name_buckets: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        name_buckets[_norm_name(node["name"])].append(node["id"])
        if not node.get("source_urls"):
            issue("error", "node_missing_source", "节点缺少 URL 来源。", node["id"])
        if node.get("confidence", 0) < MIN_CONFIDENCE:
            issue("warning", "node_low_confidence", "节点置信度低于阈值。", node["id"])
        if "company_list" in node or "公司列表" in node:
            issue("error", "company_field_present", "节点包含公司字段，当前版本不应涉及公司信息。", node["id"])
        if node.get("level", 0) < 0:
            issue("error", "invalid_level", "节点层级不能为负数。", node["id"])

    for norm_name, ids in name_buckets.items():
        if norm_name and len(ids) > 1:
            issue("warning", "duplicate_node_name", f"疑似重复节点名称：{', '.join(ids)}。", ids[0])

    pair_types: dict[tuple[str, str], set[str]] = defaultdict(set)
    incident: set[str] = set()
    children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        relation_type = edge.get("relation_type")
        edge_key = edge.get("id") or f"{source}->{target}"
        if source not in node_by_id:
            issue("error", "edge_missing_source_node", "关系起点节点不存在。", edge_key)
        if target not in node_by_id:
            issue("error", "edge_missing_target_node", "关系终点节点不存在。", edge_key)
        if relation_type not in ALLOWED_RELATIONS:
            issue("error", "invalid_relation_type", "关系类型不在允许范围内。", edge_key)
        if not edge.get("source_urls"):
            issue("error", "edge_missing_source", "关系缺少 URL 来源。", edge_key)
        if edge.get("confidence", 0) < MIN_CONFIDENCE:
            issue("warning", "edge_low_confidence", "关系置信度低于阈值。", edge_key)
        pair_types[(source, target)].add(relation_type)
        incident.add(source)
        incident.add(target)
        if relation_type == "contains" and source in node_by_id and target in node_by_id:
            children[source].append(target)

    for (source, target), relation_types in pair_types.items():
        if len(relation_types) > 1:
            issue("error", "relation_conflict", "同一节点对存在多种主关系。", f"{source}->{target}")

    for node in nodes:
        if node["id"] not in incident and node.get("chain_position") != "root":
            issue("warning", "isolated_node", "节点没有任何关系，建议进入复核队列。", node["id"])

    roots = [node["id"] for node in nodes if node.get("level") == 0 or node.get("chain_position") == "root"]
    max_depth = 0
    for root in roots:
        queue = deque([(root, 0)])
        seen = {root}
        while queue:
            node_id, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            for child_id in children.get(node_id, []):
                if child_id not in seen:
                    seen.add(child_id)
                    queue.append((child_id, depth + 1))
    if max_depth < 4:
        issue("warning", "shallow_hierarchy", "contains 层级深度低于建议的 5-6 层。")

    error_count = sum(1 for item in issues if item["severity"] == "error")
    warning_count = sum(1 for item in issues if item["severity"] == "warning")
    return {
        "industry": graph.get("industry", industry_id),
        "schema_version": graph.get("schema_version"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "max_contains_depth": max_depth,
        "error_count": error_count,
        "warning_count": warning_count,
        "status": "pass" if error_count == 0 else "fail",
        "issues": issues,
    }


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines = [
        f"# {report['industry']} 图谱校验报告",
        "",
        f"- 状态：{report['status']}",
        f"- 节点数：{report['node_count']}",
        f"- 关系数：{report['edge_count']}",
        f"- contains 最大深度：{report['max_contains_depth']}",
        f"- error：{report['error_count']}",
        f"- warning：{report['warning_count']}",
        "",
        "## 问题列表",
        "",
    ]
    if not report["issues"]:
        lines.append("未发现阻断性问题。")
    else:
        for item in report["issues"]:
            lines.append(f"- [{item['severity']}] {item['code']} {item.get('item_id', '')}：{item['message']}")

    semantic = report.get("semantic_validation")
    if semantic:
        lines.extend(["", "## 百炼校验 Agent", ""] )
        lines.append(f"- 状态：{semantic.get('validation_status', 'unknown')}")
        if semantic.get("summary"):
            lines.append(f"- 总结：{semantic['summary']}")
        lines.append(f"- 最小修改数：{len(semantic.get('modifications', []))}")
        lines.append(f"- 语义复核项：{len(semantic.get('review_items', []))}")
        if semantic.get("modifications"):
            lines.extend(["", "### 修改清单", ""] )
            for item in semantic.get("modifications", []):
                lines.append(f"- {item.get('type', 'update')} {item.get('target_id', '')}：{item.get('reason', '')}")
        if semantic.get("review_items"):
            lines.extend(["", "### 语义复核项", ""] )
            for item in semantic.get("review_items", []):
                lines.append(f"- [{item.get('severity', 'warning')}] {item.get('item_id', '')}：{item.get('reason', '')} {item.get('suggestion', '')}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_industry(industry_id: str, graph_file: Path | None = None, report_path: Path | None = None) -> dict[str, Any]:
    graph = load_graph(industry_id) if graph_file is None else __import__("json").load(graph_file.open("r", encoding="utf-8"))
    report = validate_graph(graph, industry_id)
    if report_path is not None:
        write_markdown_report(report, report_path)
        write_json(report_path.with_suffix(".json"), report)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate an industry graph JSON.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--graph-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    default_output = Path("data") / "industries" / args.industry_id / "validation_report.md"
    result = validate_industry(args.industry_id, args.graph_file, args.output or default_output)
    print(f"{result['status']}: {result['error_count']} errors, {result['warning_count']} warnings")
