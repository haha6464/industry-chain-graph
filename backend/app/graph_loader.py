import json
from pathlib import Path
from typing import Any

from app.schemas import GraphEdge, GraphNode


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "industries"
MANIFEST_PATH = DATA_ROOT / "manifest.json"


def load_manifest() -> list[dict[str, Any]]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _node_type(node: dict[str, Any]) -> str:
    if node.get("level") == 0 or node.get("chain_position") == "root":
        return "产业链"
    if node.get("level", 0) <= 1:
        return "产业链环节"
    return "细分环节"


def load_industry_graph(industry_id: str) -> tuple[str, list[GraphNode], list[GraphEdge]]:
    manifest = load_manifest()
    industry = next((item for item in manifest if item["id"] == industry_id), None)
    if industry is None:
        raise ValueError(f"Unknown industry_id: {industry_id}")

    graph_path = PROJECT_ROOT / industry["data_path"]
    with graph_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    industry_name = raw.get("industry", industry.get("name", industry_id))
    nodes = [
        GraphNode(
            id=node["id"],
            industry_id=industry_id,
            name=node["name"],
            node_type=node.get("node_type") or _node_type(node),
            tags=node.get("tags") or [f"level_{node['level']}", node["chain_position"]],
            industry=node.get("industry") or industry_name,
            level=node["level"],
            chain_position=node["chain_position"],
            chain_segment=node.get("chain_segment") or node.get("chain_position"),
            parent_id=node.get("parent_id") or None,
            description=node.get("description") or node.get("business_description", ""),
            business_description=node.get("business_description") or node.get("description", ""),
            is_key_node=bool(node.get("is_key_node", node.get("level", 0) <= 1)),
            source_urls=node.get("source_urls", []),
            evidence_ids=node.get("evidence_ids", []),
            confidence=float(node.get("confidence", 0.0)),
            updated_at=node.get("updated_at"),
        )
        for node in raw["nodes"]
    ]
    edges = [
        GraphEdge(
            id=edge.get("id") or f"{edge['source']}__{edge['relation_type']}__{edge['target']}",
            source=edge["source"],
            target=edge["target"],
            relation_type=edge["relation_type"],
            description=edge.get("description", ""),
            relation_weight=float(edge.get("relation_weight", 1.0)),
            source_urls=edge.get("source_urls", []),
            evidence_ids=edge.get("evidence_ids", []),
            confidence=float(edge.get("confidence", 0.0)),
            updated_at=edge.get("updated_at"),
        )
        for edge in raw["edges"]
    ]
    return industry_name, nodes, edges
