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

from tools.agent.common import industry_dir, load_graph, read_json, write_json
from tools.agent.l2_flow_relations import (
    L2_FLOW_SCHEMA_VERSION,
    apply_l1_l2_projection,
    graph_fingerprint,
)
from tools.agent.validators.l2_flow_relation_validator import (
    validate_l2_flow_relations,
    write_l2_flow_validation_report,
)


def _write_validation(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "l2_flow_relation_validation_report.md").write_text(
        write_l2_flow_validation_report(report), encoding="utf-8"
    )
    write_json(output_dir / "l2_flow_relation_validation_report.json", report)


def project_existing_l2_relations(industry_id: str) -> dict[str, str | int]:
    output_dir = industry_dir(industry_id)
    graph = load_graph(industry_id)
    relation_path = output_dir / "l2_flow_relations.json"
    if not relation_path.exists():
        raise FileNotFoundError("找不到 l2_flow_relations.json，请先完成 L2 上下游建边。")
    payload = read_json(relation_path)
    if payload.get("schema_version") != L2_FLOW_SCHEMA_VERSION or not isinstance(payload.get("edges"), list):
        raise ValueError("L2 上下游关系文件格式无效。")
    if payload.get("industry_id") != industry_id or payload.get("graph_fingerprint") != graph_fingerprint(graph):
        raise ValueError("L2 上下游关系与当前正式 graph.json 不匹配，请先重新运行 L2 建边。")

    result = apply_l1_l2_projection(payload, graph)
    validation = validate_l2_flow_relations(result, graph, industry_id)
    _write_validation(output_dir, validation)
    if validation.get("error_count", 0) > 0:
        raise RuntimeError(f"L1-L2 投影后处理校验失败：{validation['error_count']} 个错误。")
    write_json(output_dir / "l2_flow_relation_candidate.json", result)
    write_json(relation_path, result)
    return {
        "industry_id": industry_id,
        "l2_relation_count": len(result.get("edges", [])),
        "l1_l2_projected_relation_count": len(result.get("projected_edges", [])),
        "relations": str(relation_path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Project existing L2 flow relations to cross-level L1-L2 edges.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    result = project_existing_l2_relations(args.industry_id)
    print(
        f"[agent] L1-L2 投影后处理完成：{result['l2_relation_count']} 条 L2 关系 -> "
        f"{result['l1_l2_projected_relation_count']} 条跨层关系。",
        flush=True,
    )
