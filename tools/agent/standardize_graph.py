from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

import argparse

from tools.agent.common import graph_path, load_graph, standardize_graph, write_json


def standardize_industry_graph(industry_id: str) -> dict[str, object]:
    graph = standardize_graph(load_graph(industry_id), industry_id)
    write_json(graph_path(industry_id), graph)
    return {"industry_id": industry_id, "node_count": len(graph.get("nodes", [])), "edge_count": len(graph.get("edges", []))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upgrade graph.json to the auditable agent schema.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    print(standardize_industry_graph(args.industry_id))
