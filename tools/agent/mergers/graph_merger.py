from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.agent.common import edge_id, standardize_graph


def merge_candidate_graph(base_graph: dict[str, Any], industry_id: str, evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    graph = standardize_graph(base_graph, industry_id)
    evidence_urls = [row["url"] for row in evidence_rows if row.get("url")]
    evidence_ids = [row["evidence_id"] for row in evidence_rows if row.get("evidence_id")]
    if evidence_urls:
        for node in graph.get("nodes", []):
            node["source_urls"] = sorted(set(node.get("source_urls", [])) | set(evidence_urls))
            node["evidence_ids"] = sorted(set(node.get("evidence_ids", [])) | set(evidence_ids))
        for edge in graph.get("edges", []):
            edge["source_urls"] = sorted(set(edge.get("source_urls", [])) | set(evidence_urls))
            edge["evidence_ids"] = sorted(set(edge.get("evidence_ids", [])) | set(evidence_ids))

    name_seen: dict[str, str] = {}
    duplicate_nodes = []
    for node in graph.get("nodes", []):
        normalized = "".join(node["name"].lower().split())
        if normalized in name_seen:
            duplicate_nodes.append({"keep": name_seen[normalized], "drop": node["id"], "name": node["name"]})
        else:
            name_seen[normalized] = node["id"]

    relation_seen: dict[tuple[str, str], str] = {}
    relation_conflicts = []
    merged_edges = []
    for edge in graph.get("edges", []):
        edge["id"] = edge.get("id") or edge_id(edge["source"], edge["relation_type"], edge["target"])
        key = (edge["source"], edge["target"])
        previous = relation_seen.get(key)
        if previous and previous != edge["relation_type"]:
            relation_conflicts.append({"source": key[0], "target": key[1], "types": [previous, edge["relation_type"]]})
            continue
        relation_seen[key] = edge["relation_type"]
        merged_edges.append(edge)
    graph["edges"] = merged_edges
    graph["merge_report"] = {"duplicate_nodes": duplicate_nodes, "relation_conflicts": relation_conflicts}
    return graph


def build_review_queue(validation_report: dict[str, Any], merge_report: dict[str, Any] | None = None) -> dict[str, Any]:
    items = []
    for issue in validation_report.get("issues", []):
        if issue["severity"] != "error" and issue["code"] not in {"duplicate_node_name", "isolated_node", "shallow_hierarchy"}:
            continue
        items.append({"type": "validation_issue", **issue})
    merge_report = merge_report or {}
    for duplicate in merge_report.get("duplicate_nodes", []):
        items.append({"type": "duplicate_node_candidate", **duplicate})
    for conflict in merge_report.get("relation_conflicts", []):
        items.append({"type": "relation_conflict_candidate", **conflict})
    return {"status": "pending_review" if items else "clean", "items": items}
