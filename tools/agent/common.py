from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "industries"
MANIFEST_PATH = DATA_ROOT / "manifest.json"

CHAIN_SEGMENT_LABELS = {
    "root": "root",
    "upstream": "上游",
    "midstream": "中游",
    "downstream": "下游",
    "support": "支持",
}

UPSTREAM_DOWNSTREAM_LEVEL_ONE_POSITIONS = {"upstream", "downstream"}


def today_iso() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_manifest() -> list[dict[str, Any]]:
    return read_json(MANIFEST_PATH)


def save_manifest(items: list[dict[str, Any]]) -> None:
    write_json(MANIFEST_PATH, items)


def industry_dir(industry_id: str) -> Path:
    return DATA_ROOT / industry_id


def graph_path(industry_id: str) -> Path:
    return industry_dir(industry_id) / "graph.json"


def load_graph(industry_id: str) -> dict[str, Any]:
    return read_json(graph_path(industry_id))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def edge_id(source: str, relation_type: str, target: str) -> str:
    return f"{source}__{relation_type}__{target}"


def default_source_urls(raw_graph: dict[str, Any]) -> list[str]:
    urls = []
    for source in raw_graph.get("source_basis", []):
        url = source.get("url")
        if url and url not in urls:
            urls.append(url)
    return urls


def default_evidence_ids(source_urls: list[str]) -> list[str]:
    return [f"source_{index + 1:03d}" for index, _ in enumerate(source_urls)]


def node_type_for(node: dict[str, Any]) -> str:
    if node.get("level") == 0 or node.get("chain_position") == "root":
        return "产业链"
    if node.get("level", 0) <= 1:
        return "产业链环节"
    return "细分环节"


def _is_root_node(node: dict[str, Any]) -> bool:
    return int(node.get("level", 0)) == 0 or node.get("chain_position") == "root"


def _is_level_one_updown(node: dict[str, Any]) -> bool:
    return int(node.get("level", 0)) == 1 and node.get("chain_position") in UPSTREAM_DOWNSTREAM_LEVEL_ONE_POSITIONS


def standardize_graph(raw_graph: dict[str, Any], industry_id: str) -> dict[str, Any]:
    source_urls = default_source_urls(raw_graph)
    evidence_ids = default_evidence_ids(source_urls)
    updated_at = raw_graph.get("generated_at") or today_iso()
    industry_name = raw_graph.get("industry") or industry_id

    nodes = []
    for node in raw_graph.get("nodes", []):
        description = node.get("business_description") or node.get("description") or ""
        chain_position = node.get("chain_position", "support")
        level = int(node.get("level", 0))
        node_urls = node.get("source_urls") or source_urls
        node_evidence = node.get("evidence_ids") or evidence_ids
        nodes.append(
            {
                "id": node["id"],
                "name": node["name"],
                "node_type": node.get("node_type") or node_type_for(node),
                "tags": node.get("tags") or [f"level_{level}", chain_position],
                "industry": node.get("industry") or industry_name,
                "level": level,
                "chain_position": chain_position,
                "parent_id": node.get("parent_id") or "",
                "description": description,
                "business_description": description,
                "is_key_node": bool(node.get("is_key_node", level <= 1)),
                "chain_segment": node.get("chain_segment") or CHAIN_SEGMENT_LABELS.get(chain_position, chain_position),
                "source_urls": node_urls,
                "evidence_ids": node_evidence,
                "confidence": float(node.get("confidence", 0.75 if node_urls else 0.0)),
                "updated_at": node.get("updated_at") or updated_at,
            }
        )

    node_lookup = {node["id"]: node for node in nodes}
    root_ids = [node["id"] for node in nodes if _is_root_node(node)]
    root_id = root_ids[0] if root_ids else ""

    def top_level_branch(node_id: str) -> str | None:
        current_id = node_id
        visited: set[str] = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            current = node_lookup.get(current_id)
            if not current:
                return None
            if int(current.get("level", 0)) == 1:
                return current_id
            parent_id = current.get("parent_id") or ""
            if not parent_id:
                return None
            current_id = parent_id
        return None

    raw_edges = sorted(raw_graph.get("edges", []), key=lambda item: 0 if item.get("relation_type") == "contains" else 1)

    for edge in raw_edges:
        if edge.get("relation_type") != "contains":
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source in node_lookup and target in node_lookup and not node_lookup[target].get("parent_id"):
            node_lookup[target]["parent_id"] = source

    # Mentor relation semantics: upstream/downstream is reserved for judging whether
    # a level=1 branch is upstream or downstream of the industry root. For those
    # level=1 branches, do not also keep a root contains edge; otherwise the
    # DOWNSTREAM_OF and SUBORDINATE_TO meanings overlap in CSV/Neo4j.
    for node in nodes:
        if _is_level_one_updown(node):
            node["parent_id"] = ""
        elif int(node.get("level", 0)) == 1 and not node.get("parent_id") and root_id:
            node["parent_id"] = root_id

    parent_contains_pairs = {
        (node.get("parent_id"), node_id)
        for node_id, node in node_lookup.items()
        if node.get("parent_id") and node.get("parent_id") in node_lookup
    }
    raw_edge_by_key = {
        (edge.get("source"), edge.get("target"), edge.get("relation_type")): edge
        for edge in raw_edges
    }
    tree_edges = [
        raw_edge_by_key.get((source, target, "contains"), {"source": source, "target": target, "relation_type": "contains"})
        for source, target in sorted(parent_contains_pairs)
    ]
    level_one_flow_keys: set[tuple[str, str, str]] = set()
    if root_id:
        for node in nodes:
            if not _is_level_one_updown(node):
                continue
            if node.get("chain_position") == "upstream":
                level_one_flow_keys.add((node["id"], "upstream_downstream", root_id))
            elif node.get("chain_position") == "downstream":
                level_one_flow_keys.add((root_id, "upstream_downstream", node["id"]))
    flow_edges = [
        raw_edge_by_key.get((source, target, relation_type), {"source": source, "target": target, "relation_type": relation_type})
        for source, relation_type, target in sorted(level_one_flow_keys)
    ]
    raw_edges = [*tree_edges, *flow_edges]
    contains_node_pairs = {frozenset(pair) for pair in parent_contains_pairs}
    edges = []
    seen_node_pairs: set[frozenset[str]] = set()
    for edge in raw_edges:
        source = edge["source"]
        target = edge["target"]
        relation_type = edge["relation_type"]
        if source not in node_lookup or target not in node_lookup:
            continue
        node_pair = frozenset((source, target))
        if node_pair in seen_node_pairs:
            continue
        if relation_type == "contains":
            if (source, target) not in parent_contains_pairs:
                continue
        elif relation_type == "upstream_downstream":
            if node_pair in contains_node_pairs:
                continue
            source_level = int(node_lookup[source].get("level", 0))
            target_level = int(node_lookup[target].get("level", 0))
            if not ((source_level == 1 and target_level == 0) or (source_level == 0 and target_level == 1)):
                continue
            if root_id and source != root_id and target != root_id:
                continue
        seen_node_pairs.add(node_pair)
        edge_urls = edge.get("source_urls") or sorted(
            set(node_lookup.get(source, {}).get("source_urls", []))
            | set(node_lookup.get(target, {}).get("source_urls", []))
            | set(source_urls)
        )
        edge_evidence = edge.get("evidence_ids") or default_evidence_ids(edge_urls)
        edges.append(
            {
                "id": edge.get("id") or edge_id(source, relation_type, target),
                "source": source,
                "target": target,
                "relation_type": relation_type,
                "relation_weight": float(edge.get("relation_weight", 1.0)),
                "description": edge.get("description") or "",
                "source_urls": edge_urls,
                "evidence_ids": edge_evidence,
                "confidence": float(edge.get("confidence", 0.75 if edge_urls else 0.0)),
                "updated_at": edge.get("updated_at") or updated_at,
            }
        )

    standardized_graph = dict(raw_graph)
    standardized_graph["version"] = raw_graph.get("version", "v0.1-demo")
    standardized_graph["schema_version"] = "standard_industry_graph_v0.2_agent"
    standardized_graph["generated_at"] = updated_at
    standardized_graph["scope"] = raw_graph.get(
        "scope",
        "标清产业链图谱；不包含公司节点、股票代码、财务指标。",
    )
    standardized_graph["nodes"] = nodes
    standardized_graph["edges"] = edges
    return standardized_graph



