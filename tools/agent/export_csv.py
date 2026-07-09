
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, load_graph, standardize_graph

def _convert_node_id(node_id: str) -> str:
    """将节点 ID 转换为大写前缀 + 6位数字格式，例如 fb_000 -> FB000000。"""
    m = re.match(r"^([A-Za-z]+)[_\-]?(\d+)$", node_id)
    if m:
        prefix, num = m.group(1).upper(), m.group(2)
        return f"{prefix}{num.zfill(6)}"
    return node_id


NODE_FIELDS = ["节点id", "节点类型", "节点名称", "节点标签", "节点行业", "业务描述", "关键节点", "产业链环节"]
EDGE_FIELDS = ["起点节点id", "起点节点名称", "终点节点id", "终点节点名称", "关系类型", "关系权重", "关系描述"]


def _safe_filename(value: str) -> str:
    return "".join(ch for ch in value if ch not in r'<>:"/\\|?*').strip() or "industry"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    actual_path = path
    try:
        file = actual_path.open("w", encoding="utf-8-sig", newline="")
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d%H%M%S")
        actual_path = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        print(f"[agent] CSV 文件被占用，已改写备用文件：{actual_path}", flush=True)
        file = actual_path.open("w", encoding="utf-8-sig", newline="")
    with file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return actual_path


def export_graph_csv(graph: dict[str, Any], industry_id: str, output_dir: Path | None = None) -> dict[str, str]:
    graph = standardize_graph(graph, industry_id)
    industry_name = graph.get("industry", industry_id)
    output_dir = output_dir or industry_dir(industry_id) / "exports"
    prefix = _safe_filename(industry_name.replace("行业", "") + "产业链图谱")
    node_path = output_dir / f"{prefix}_graph_node.csv"
    edge_path = output_dir / f"{prefix}_graph_edge.csv"

    # 构建原始 ID -> 转换后 ID 的映射
    id_map: dict[str, str] = {}
    for node in graph.get("nodes", []):
        id_map[node["id"]] = _convert_node_id(node["id"])

    node_rows = []
    node_lookup = {}
    for node in graph.get("nodes", []):
        converted_id = id_map[node["id"]]
        node_lookup[node["id"]] = node
        node_rows.append(
            {
                "节点id": converted_id,
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
        start_id = id_map.get(edge["target"], edge["target"])
        end_id = id_map.get(edge["source"], edge["source"])
        if edge["relation_type"] == "contains":
            relation_type = "SUBORDINATE_TO"
        else:
            relation_type = "DOWNSTREAM_OF"
        start = node_lookup.get(edge["target"], {})
        end = node_lookup.get(edge["source"], {})
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

    node_path = _write_csv(node_path, NODE_FIELDS, node_rows)
    edge_path = _write_csv(edge_path, EDGE_FIELDS, edge_rows)
    return {"industry_id": industry_id, "node_csv": str(node_path), "edge_csv": str(edge_path)}


def export_industry_csv(industry_id: str, output_dir: Path | None = None) -> dict[str, str]:
    return export_graph_csv(load_graph(industry_id), industry_id, output_dir)


def _print_export_summary(result: dict[str, str]) -> None:
    print(f"[agent] CSV 导出完成：{result.get('industry_id', '')}。", flush=True)
    print("[agent] 已生成产物：节点 CSV、关系 CSV。", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export mentor CSV files from graph.json.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = export_industry_csv(args.industry_id, args.output_dir)
    _print_export_summary(result)




