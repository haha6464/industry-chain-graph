
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, load_graph, standardize_graph

NODE_FIELDS = ["节点id", "节点类型", "节点名称", "节点标签", "节点行业", "业务描述", "关键节点", "产业链环节"]
EDGE_FIELDS = ["起点节点id", "起点节点名称", "终点节点id", "终点节点名称", "关系类型", "关系权重", "关系描述"]


def _safe_filename(value: str) -> str:
    return "".join(ch for ch in value if ch not in r'<>:"/\\|?*').strip() or "industry"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_graph_csv(graph: dict[str, Any], industry_id: str, output_dir: Path | None = None) -> dict[str, str]:
    graph = standardize_graph(graph, industry_id)
    industry_name = graph.get("industry", industry_id)
    output_dir = output_dir or industry_dir(industry_id) / "exports"
    prefix = _safe_filename(industry_name.replace("行业", "") + "产业链图谱")
    node_path = output_dir / f"{prefix}_graph_node.csv"
    edge_path = output_dir / f"{prefix}_graph_edge.csv"

    node_rows = []
    node_lookup = {}
    for node in graph.get("nodes", []):
        node_lookup[node["id"]] = node
        node_rows.append(
            {
                "节点id": node["id"],
                "节点类型": node.get("node_type", "产业链环节"),
                "节点名称": node["name"],
                "节点标签": ";".join(node.get("tags", [])),
                "节点行业": node.get("industry") or industry_name,
                "业务描述": node.get("business_description") or node.get("description", ""),
                "关键节点": "true" if node.get("is_key_node") else "false",
                "产业链环节": node.get("chain_segment") or node.get("chain_position", ""),
            }
        )

    edge_rows = []
    for edge in graph.get("edges", []):
        if edge["relation_type"] == "contains":
            start_id, end_id = edge["target"], edge["source"]
            relation_type = "SUBORDINATE_TO"
        else:
            start_id, end_id = edge["target"], edge["source"]
            relation_type = "DOWNSTREAM_OF"
        start = node_lookup.get(start_id, {})
        end = node_lookup.get(end_id, {})
        edge_rows.append(
            {
                "起点节点id": start_id,
                "起点节点名称": start.get("name", ""),
                "终点节点id": end_id,
                "终点节点名称": end.get("name", ""),
                "关系类型": relation_type,
                "关系权重": edge.get("relation_weight", 1.0),
                "关系描述": edge.get("description", ""),
            }
        )

    _write_csv(node_path, NODE_FIELDS, node_rows)
    _write_csv(edge_path, EDGE_FIELDS, edge_rows)
    return {"industry_id": industry_id, "node_csv": str(node_path), "edge_csv": str(edge_path)}


def export_industry_csv(industry_id: str, output_dir: Path | None = None) -> dict[str, str]:
    return export_graph_csv(load_graph(industry_id), industry_id, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export mentor CSV files from graph.json.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = export_industry_csv(args.industry_id, args.output_dir)
    print(result)
