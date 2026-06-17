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


def load_industry_graph(industry_id: str) -> tuple[str, list[GraphNode], list[GraphEdge]]:
    manifest = load_manifest()
    industry = next((item for item in manifest if item["id"] == industry_id), None)
    if industry is None:
        raise ValueError(f"Unknown industry_id: {industry_id}")

    graph_path = PROJECT_ROOT / industry["data_path"]
    with graph_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    nodes = [
        GraphNode(
            id=node["id"],
            industry_id=industry_id,
            name=node["name"],
            level=node["level"],
            chain_position=node["chain_position"],
            parent_id=node.get("parent_id") or None,
            description=node["description"],
        )
        for node in raw["nodes"]
    ]
    edges = [
        GraphEdge(
            id=f"{edge['source']}__{edge['relation_type']}__{edge['target']}",
            source=edge["source"],
            target=edge["target"],
            relation_type=edge["relation_type"],
            description=edge["description"],
        )
        for edge in raw["edges"]
    ]
    return raw["industry"], nodes, edges

