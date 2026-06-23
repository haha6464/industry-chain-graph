from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

import argparse

from tools.agent.common import edge_id, industry_dir, load_graph, read_jsonl, standardize_graph, write_json, write_jsonl
from tools.agent.export_csv import export_graph_csv, export_industry_csv
from tools.agent.updaters.bailian_update_agent import call_bailian_update_agent
from tools.agent.validators.graph_validator import validate_graph, write_markdown_report


def _index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items if item.get("id")}


def _apply_update_proposal(graph: dict[str, Any], proposal: dict[str, Any], industry_id: str) -> dict[str, Any]:
    graph = standardize_graph(graph, industry_id)
    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    node_by_id = _index_by_id(nodes)
    edge_by_id = {edge.get("id") or edge_id(edge["source"], edge["relation_type"], edge["target"]): edge for edge in edges}

    for node in proposal.get("add_nodes", []) or []:
        if node.get("id") and node["id"] not in node_by_id:
            nodes.append(node)
            node_by_id[node["id"]] = node

    for edge in proposal.get("add_edges", []) or []:
        edge["id"] = edge.get("id") or edge_id(edge["source"], edge["relation_type"], edge["target"])
        if edge["id"] not in edge_by_id:
            edges.append(edge)
            edge_by_id[edge["id"]] = edge

    for item in proposal.get("modify_nodes", []) or []:
        node = node_by_id.get(item.get("id", ""))
        if node:
            node.update(item.get("patch", {}))

    for item in proposal.get("modify_edges", []) or []:
        target_id = item.get("id") or edge_id(item.get("source", ""), item.get("relation_type", "upstream_downstream"), item.get("target", ""))
        edge = edge_by_id.get(target_id)
        if edge:
            edge.update(item.get("patch", {}))

    for item in proposal.get("remove_or_deprecate", []) or []:
        item_id = item.get("id")
        if item.get("item_type") == "node" and item_id in node_by_id:
            node_by_id[item_id]["deprecated"] = True
        if item.get("item_type") == "edge" and item_id in edge_by_id:
            edge_by_id[item_id]["deprecated"] = True

    graph["nodes"] = nodes
    graph["edges"] = edges
    return standardize_graph(graph, industry_id)


def _write_update_report(path: Path, proposal: dict[str, Any], validation: dict[str, Any], applied: bool) -> None:
    lines = [
        f"# {proposal.get('industry_id', '')} 更新检查报告",
        "",
        f"- 状态：{proposal.get('status')}",
        f"- 是否写回 graph.json：{applied}",
        f"- 新增节点：{len(proposal.get('add_nodes', []))}",
        f"- 新增关系：{len(proposal.get('add_edges', []))}",
        f"- 修改节点：{len(proposal.get('modify_nodes', []))}",
        f"- 修改关系：{len(proposal.get('modify_edges', []))}",
        f"- 废弃项：{len(proposal.get('remove_or_deprecate', []))}",
        f"- 复核项：{len(proposal.get('review_items', []))}",
        f"- 应用后硬规则状态：{validation.get('status')}",
        "",
        "## 原因",
        "",
        proposal.get("reason", ""),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_graph(industry_id: str, mode: str) -> dict[str, object]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    graph = load_graph(industry_id)
    existing_sources = read_jsonl(output_dir / "sources.jsonl")
    proposal, raw_text = call_bailian_update_agent(graph, existing_sources, mode)
    proposal["industry_id"] = industry_id
    raw_path = output_dir / "update_agent_raw_response.txt"
    raw_path.write_text(raw_text, encoding="utf-8")

    proposal_path = output_dir / "update_proposal.json"
    write_json(proposal_path, proposal)

    candidate = _apply_update_proposal(graph, proposal, industry_id)
    validation = validate_graph(candidate, industry_id)
    candidate_path = output_dir / "update_candidate_graph.json"
    write_json(candidate_path, candidate)

    applied = False
    can_apply = (
        mode == "apply"
        and proposal.get("status") in {"proposed", "apply_ready"}
        and validation.get("error_count", 1) == 0
        and not any(item.get("severity") == "error" for item in proposal.get("review_items", []))
    )
    if can_apply:
        write_json(output_dir / "graph.json", candidate)
        write_jsonl(output_dir / "sources.jsonl", existing_sources + proposal.get("checked_sources", []))
        applied = True
        export = export_graph_csv(candidate, industry_id)
    else:
        export = export_industry_csv(industry_id)

    report_path = output_dir / "update_report.md"
    _write_update_report(report_path, proposal, validation, applied)
    validation_path = output_dir / "validation_report.md"
    write_markdown_report(validation, validation_path)
    write_json(validation_path.with_suffix(".json"), validation)

    return {
        "industry_id": industry_id,
        "status": proposal.get("status", "needs_review"),
        "proposal": str(proposal_path),
        "update_report": str(report_path),
        "update_candidate_graph": str(candidate_path),
        "update_agent_raw_response": str(raw_path),
        "applied": applied,
        **export,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Bailian web-search based incremental update proposal.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--mode", choices=["check_only", "propose", "apply"], default="check_only")
    args = parser.parse_args()
    result = update_graph(args.industry_id, args.mode)
    print(result)
