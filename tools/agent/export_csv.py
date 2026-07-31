
from __future__ import annotations

import argparse
import csv
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

from tools.agent.common import PROJECT_ROOT, industry_dir, load_graph, load_manifest
from tools.agent.company_attachments import (
    ancestors_for_node,
    attachment_file_status,
    filter_listed_attachments,
)
from tools.agent.l2_flow_relations import relation_file_status

INDUSTRY_NODE_FIELDS = ["code", "name", "ind_id"]
INDUSTRYNODE_EDGE_FIELDS = ["起点节点id", "起点节点名称", "终点节点id", "终点节点名称", "关系类型", "关系权重", "关系描述", "置信度", "强度", "开始时间", "结束时间"]
INDUSTRYNODE_INDUSTRY_EDGE_FIELDS = ["起点节点id", "起点节点名称", "终点节点code", "终点节点名称", "关系类型", "开始时间", "结束时间"]
INDUSTRYNODE_NODE_FIELDS = ["节点id", "节点类型", "节点名称", "节点标签", "节点行业", "业务描述", "公司列表", "关键节点", "产业链环节", "节点行业code", "生效时间", "失效时间"]
COMPANY_NODE_FIELDS = ["节点code", "节点类型", "节点名称", "证券代码", "生效时间", "失效时间", "业务描述"]
COMPANY_EDGE_FIELDS = ["起点节点code", "起点节点名称", "终点节点id", "终点节点名称", "主体产业链关系", "开始时间", "结束时间", "数据来源"]

# ind_id follows the delivery-side industry mapping shown in the supplied template.
# Add an explicit row before exporting a newly onboarded industry.
INDUSTRY_EXPORT_METADATA: dict[str, dict[str, str]] = {
    "food_beverage": {"code": "FOOD", "name": "食品饮料", "ind_id": "041800"},
}


def _safe_filename(value: str) -> str:
    return "".join(ch for ch in value if ch not in r'<>:"/\\|?*').strip() or "industry"


def delivery_output_dir(industry_id: str) -> Path:
    """Return the only persistent location for an industry's delivery CSVs."""
    item = next((entry for entry in load_manifest() if str(entry.get("id")) == industry_id), None)
    if item is None:
        raise ValueError(f"未知行业：{industry_id}")
    return PROJECT_ROOT / "deliverables" / f"{_safe_filename(str(item.get('name') or industry_id))}图谱"


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


def _industry_metadata(industry_id: str) -> dict[str, str]:
    metadata = INDUSTRY_EXPORT_METADATA.get(industry_id)
    if metadata is None:
        raise ValueError(f"缺少行业交付映射：{industry_id}。请配置 code、name 和 ind_id。")
    return metadata


def _delivery_node_id(node_id: str, industry_id: str, industry_code: str) -> str:
    prefix = f"{industry_id}_"
    suffix = node_id[len(prefix):] if node_id.startswith(prefix) else node_id
    tokens = suffix.split("_")
    if tokens and all(token.isdigit() for token in tokens):
        numeric = "".join(f"{int(token):03d}" for token in tokens)
        return f"{industry_code}{numeric.zfill(6)}"
    raise ValueError(f"产业链节点 ID 不符合交付编码规则：{node_id}")


def _node_chain_segment(node: dict[str, Any]) -> str:
    if int(node.get("level", -1)) != 1:
        return ""
    return {
        "upstream": "上游",
        "midstream": "中游",
        "downstream": "下游",
        "support": "支持",
        "root": "",
    }.get(str(node.get("chain_position", "")), str(node.get("chain_segment", "")))


def export_graph_csv(
    graph: dict[str, Any],
    industry_id: str,
    output_dir: Path | None = None,
    extra_edges: list[dict[str, Any]] | None = None,
    company_attachments: dict[str, Any] | None = None,
) -> dict[str, str]:
    # ``graph.json`` is already the formal, standardized graph.  Re-running the
    # builder normalization here would remove singleton leaves, including leaves
    # with valid company attachments, and make CSV disagree with the canvas.
    graph = dict(graph)
    metadata = _industry_metadata(industry_id)
    industry_name, industry_code = metadata["name"], metadata["code"]
    effective_date = str(graph.get("generated_at", ""))[:10] or datetime.now().date().isoformat()
    output_dir = output_dir or delivery_output_dir(industry_id)
    prefix = _safe_filename(industry_name)
    industry_node_path = output_dir / f"{prefix}_industry_node.csv"
    edge_path = output_dir / f"{prefix}_industrynode_edge.csv"
    industry_edge_path = output_dir / f"{prefix}_industrynode_industry_edge.csv"
    node_path = output_dir / f"{prefix}_industrynode_node.csv"
    id_map = {
        str(node["id"]): _delivery_node_id(str(node["id"]), industry_id, industry_code)
        for node in graph.get("nodes", [])
    }

    node_rows = []
    node_lookup = {}
    for node in graph.get("nodes", []):
        raw_id = str(node["id"])
        converted_id = id_map[raw_id]
        node_lookup[raw_id] = node
        node_rows.append(
            {
                "节点id": converted_id,
                # Mentor delivery convention: every industry-graph node uses the
                # same node type; hierarchy is expressed exclusively by level_N.
                "节点类型": "产业链",
                "节点名称": node["name"],
                "节点标签": f"level_{int(node.get('level', 0))}",
                "节点行业": industry_name,
                "业务描述": node.get("business_description") or node.get("description", ""),
                # Company relationships are delivered in their dedicated CSV;
                # this column is intentionally blank in the mentor template.
                "公司列表": "",
                "关键节点": "TRUE" if node.get("is_key_node") else "FALSE",
                "产业链环节": _node_chain_segment(node),
                "节点行业code": industry_code,
                "生效时间": effective_date,
                "失效时间": "",
            }
        )

    edge_rows = []
    seen_edge_ids: set[str] = set()
    for edge in [*graph.get("edges", []), *(extra_edges or [])]:
        edge_identifier = str(edge.get("id") or f"{edge.get('source')}->{edge.get('target')}")
        if edge_identifier in seen_edge_ids or edge.get("source") not in node_lookup or edge.get("target") not in node_lookup:
            continue
        seen_edge_ids.add(edge_identifier)
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
                "置信度": edge.get("confidence", ""),
                "强度": edge.get("strength", ""),
                "开始时间": effective_date,
                "结束时间": "",
            }
        )

    industry_node_path = _write_csv(
        industry_node_path,
        INDUSTRY_NODE_FIELDS,
        [{"code": industry_code, "name": industry_name, "ind_id": metadata["ind_id"]}],
    )
    node_path = _write_csv(node_path, INDUSTRYNODE_NODE_FIELDS, node_rows)
    edge_path = _write_csv(edge_path, INDUSTRYNODE_EDGE_FIELDS, edge_rows)
    industry_edge_path = _write_csv(
        industry_edge_path,
        INDUSTRYNODE_INDUSTRY_EDGE_FIELDS,
        [
            {
                "起点节点id": id_map[str(node["id"])],
                "起点节点名称": node["name"],
                "终点节点code": industry_code,
                "终点节点名称": industry_name,
                "关系类型": "BELONGS_TO_INDUSTRY",
                "开始时间": effective_date,
                "结束时间": "",
            }
            for node in graph.get("nodes", [])
        ],
    )
    return {
        "industry_id": industry_id,
        "industry_node_csv": str(industry_node_path),
        "industrynode_edge_csv": str(edge_path),
        "industrynode_industry_edge_csv": str(industry_edge_path),
        "industrynode_node_csv": str(node_path),
        # Compatibility aliases for existing callers.
        "node_csv": str(node_path),
        "edge_csv": str(edge_path),
    }


def export_company_attachment_csv(
    graph: dict[str, Any],
    attachments: dict[str, Any],
    industry_id: str,
    output_dir: Path | None = None,
    listed_only: bool = True,
) -> dict[str, str]:
    """Export direct company attachments plus their aggregation-parent edges.

    Non-domestic-listed companies are dropped by default. The filtering reuses
    ``filter_listed_attachments`` so the CSV and the filter workflow can never
    disagree about what counts as listed.
    """
    if listed_only:
        attachments, stats = filter_listed_attachments(attachments)
        if stats["company_removed"] or stats["attachment_removed"]:
            print(
                f"[agent] CSV 仅导出境内上市公司：公司 {stats['company_count_before']} → "
                f"{stats['company_count_after']}，挂载 {stats['attachment_count_before']} → "
                f"{stats['attachment_count_after']}。",
                flush=True,
            )
    # Keep the complete formal graph for the same reason as ``export_graph_csv``.
    graph = dict(graph)
    metadata = _industry_metadata(industry_id)
    output_dir = output_dir or delivery_output_dir(industry_id)
    prefix = _safe_filename(metadata["name"])
    company_node_path = output_dir / f"{prefix}_company_node.csv"
    company_edge_path = output_dir / f"{prefix}_company_industrynode_edge_node.csv"
    effective_date = str(attachments.get("generated_at", ""))[:10] or datetime.now().date().isoformat()
    company_by_id = {str(item.get("company_id")): item for item in attachments.get("companies", [])}
    node_by_id = {str(item.get("id")): item for item in graph.get("nodes", [])}

    attached_company_ids = {
        str(item.get("company_id"))
        for item in attachments.get("attachments", []) or []
        if item.get("company_id") in company_by_id and item.get("node_id") in node_by_id
    }
    company_rows = [
        {
            "节点code": company_by_id[company_id].get("comcode", ""),
            "节点类型": "公司",
            "节点名称": company_by_id[company_id].get("name", ""),
            "证券代码": "",
            "生效时间": effective_date,
            "失效时间": "",
            "业务描述": "",
        }
        for company_id in sorted(attached_company_ids, key=lambda item: str(company_by_id[item].get("comcode", "")))
    ]
    edge_rows = []
    seen_pairs: set[tuple[str, str]] = set()
    for attachment in attachments.get("attachments", []) or []:
        company_id = str(attachment.get("company_id", ""))
        node_id = str(attachment.get("node_id", ""))
        if company_id not in company_by_id or node_id not in node_by_id:
            continue
        company = company_by_id[company_id]
        # Preserve the direct attachment and add every classification parent.
        # ``ancestors_for_node`` additionally maps the L0--L1 main-flow boundary
        # to L0, but never treats L1--L2 flow edges as aggregation parents.
        for aggregate_node_id in [node_id, *ancestors_for_node(graph, node_id)]:
            pair = (company_id, aggregate_node_id)
            if pair in seen_pairs or aggregate_node_id not in node_by_id:
                continue
            seen_pairs.add(pair)
            node = node_by_id[aggregate_node_id]
            edge_rows.append(
                {
                    "起点节点code": company.get("comcode", ""),
                    "起点节点名称": company.get("name", ""),
                    "终点节点id": _delivery_node_id(aggregate_node_id, industry_id, metadata["code"]),
                    "终点节点名称": node.get("name", ""),
                    "主体产业链关系": "BELONGS_TO_IND_NODE",
                    "开始时间": effective_date,
                    "结束时间": "",
                    "数据来源": "联网搜索",
                }
            )
    company_node_path = _write_csv(company_node_path, COMPANY_NODE_FIELDS, company_rows)
    company_edge_path = _write_csv(company_edge_path, COMPANY_EDGE_FIELDS, edge_rows)
    return {"company_node_csv": str(company_node_path), "company_edge_csv": str(company_edge_path)}


def export_industry_csv(
    industry_id: str, output_dir: Path | None = None, listed_only: bool = True
) -> dict[str, str]:
    graph = load_graph(industry_id)
    relation_path = industry_dir(industry_id) / "l2_flow_relations.json"
    relation_status, relation_payload, _ = relation_file_status(industry_id, graph, relation_path)
    extra_edges = (
        list(relation_payload.get("edges", [])) + list(relation_payload.get("projected_edges", []))
        if relation_status == "ready" and relation_payload
        else []
    )
    attachment_path = industry_dir(industry_id) / "company_attachments.json"
    status, attachments, _ = attachment_file_status(industry_id, graph, attachment_path)
    if status == "ready" and attachments is not None and listed_only:
        attachments, _ = filter_listed_attachments(attachments)
    result = export_graph_csv(
        graph,
        industry_id,
        output_dir,
        extra_edges=extra_edges,
        company_attachments=attachments if status == "ready" else None,
    )
    if status == "ready" and attachments is not None:
        result.update(
            export_company_attachment_csv(
                graph, attachments, industry_id, output_dir, listed_only=False
            )
        )
    return result


def _print_export_summary(result: dict[str, str]) -> None:
    print(f"[agent] CSV 导出完成：{result.get('industry_id', '')}。", flush=True)
    labels = ["行业节点 CSV", "产业链节点关系 CSV", "产业链节点—行业关系 CSV", "产业链节点 CSV"]
    if result.get("company_node_csv"):
        labels.extend(["公司节点 CSV", "公司—产业链节点关系 CSV"])
    print("[agent] 已生成产物：" + "、".join(labels) + "。", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export mentor CSV files from graph.json.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--include-unlisted",
        action="store_true",
        help="公司 CSV 同时导出非境内上市公司（默认只导出境内上市公司）。",
    )
    args = parser.parse_args()
    result = export_industry_csv(args.industry_id, args.output_dir, listed_only=not args.include_unlisted)
    _print_export_summary(result)
