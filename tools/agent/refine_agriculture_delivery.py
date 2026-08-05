"""Apply the approved agriculture delivery taxonomy corrections.

This is an explicit, rerunnable data migration rather than an LLM edit.  It
updates both the current formal graph and the current candidate graph when it
exists; subsequent L2 relation and company-attachment runs rebuild every
derived attachment against the changed formal graph.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, read_json, standardize_graph, write_json


AGRICULTURE_INDUSTRY_ID = "agriculture_forestry_animal_fishery"
FEED_BRANCH_NAME = "饲料工业"
PROCESSING_BRANCH_OLD_NAME = "农产品加工与仓储"
PROCESSING_BRANCH_NEW_NAME = "农产品加工"
COLD_CHAIN_NODE_NAME = "农产品冷链仓储"


def _descendant_ids(nodes: list[dict[str, Any]], ancestor_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for node in nodes:
        node_id, parent_id = str(node.get("id", "")), str(node.get("parent_id", ""))
        if node_id and parent_id:
            children.setdefault(parent_id, []).append(node_id)
    result, pending = {ancestor_id}, [ancestor_id]
    while pending:
        current = pending.pop()
        for child_id in children.get(current, []):
            if child_id not in result:
                result.add(child_id)
                pending.append(child_id)
    return result


def refine_graph(graph: dict[str, Any], industry_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes = [dict(node) for node in graph.get("nodes", [])]
    edges = [dict(edge) for edge in graph.get("edges", [])]
    feed = next((node for node in nodes if node.get("name") == FEED_BRANCH_NAME and int(node.get("level", -1)) == 1), None)
    processing = next(
        (node for node in nodes if node.get("name") == PROCESSING_BRANCH_OLD_NAME and int(node.get("level", -1)) == 1),
        None,
    )
    cold_chain = next((node for node in nodes if node.get("name") == COLD_CHAIN_NODE_NAME), None)
    if feed is None or processing is None:
        raise ValueError("未找到“饲料工业”或“农产品加工与仓储”一级节点，已停止迁移。")

    feed["chain_position"] = "upstream"
    feed["chain_segment"] = "上游"
    feed["parent_id"] = ""
    feed["tags"] = ["level_1", "upstream"]
    processing["name"] = PROCESSING_BRANCH_NEW_NAME
    processing["description"] = "农产品初加工、深加工及相关加工制品环节。"
    processing["business_description"] = processing["description"]

    removed_ids: set[str] = set()
    if cold_chain is not None:
        removed_ids = _descendant_ids(nodes, str(cold_chain["id"]))
        nodes = [node for node in nodes if str(node.get("id")) not in removed_ids]
        edges = [
            edge for edge in edges
            if str(edge.get("source")) not in removed_ids and str(edge.get("target")) not in removed_ids
        ]
    refined = standardize_graph({**graph, "nodes": nodes, "edges": edges}, industry_id)
    report = {
        "feed_branch": {"id": str(feed["id"]), "name": FEED_BRANCH_NAME, "chain_position": "upstream"},
        "processing_branch": {
            "id": str(processing["id"]),
            "old_name": PROCESSING_BRANCH_OLD_NAME,
            "new_name": PROCESSING_BRANCH_NEW_NAME,
        },
        "removed_cold_chain_node_ids": sorted(removed_ids),
    }
    return refined, report


def run(industry_id: str) -> dict[str, Any]:
    if industry_id != AGRICULTURE_INDUSTRY_ID:
        raise ValueError("该受控迁移仅适用于农林牧渔行业。")
    output_dir = industry_dir(industry_id)
    report: dict[str, Any] = {"industry_id": industry_id, "updated_files": []}
    for filename in ("graph.json", "candidate_graph.json"):
        path = output_dir / filename
        if not path.exists():
            continue
        refined, change_report = refine_graph(read_json(path), industry_id)
        write_json(path, refined)
        report["updated_files"].append(filename)
        report["changes"] = change_report
    if "graph.json" not in report["updated_files"]:
        raise FileNotFoundError("找不到正式 graph.json，无法执行农林牧渔交付迁移。")
    write_json(output_dir / "agriculture_delivery_refinement_report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply approved agriculture delivery corrections.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    result = run(args.industry_id)
    print(f"[agent] 农林牧渔交付迁移完成：{', '.join(result['updated_files'])}", flush=True)
