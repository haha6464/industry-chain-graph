"""Keep graph attachments usable after deterministic node cleanup, without calling a model."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _bootstrap_project_root() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return


_bootstrap_project_root()

from tools.agent.common import (  # noqa: E402
    industry_dir,
    load_graph,
    now_iso,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from tools.agent.company_attachments import graph_fingerprint as company_graph_fingerprint  # noqa: E402
from tools.agent.l2_flow_relations import (  # noqa: E402
    apply_l1_l2_projection,
    build_payload,
    compact_l2_catalog,
)
from tools.agent.validators.company_attachment_validator import (  # noqa: E402
    validate_company_attachments,
    write_company_attachment_report,
)
from tools.agent.validators.l2_flow_relation_validator import (  # noqa: E402
    validate_l2_flow_relations,
    write_l2_flow_validation_report,
)


def _nearest_surviving_node(node_id: str, valid_node_ids: set[str], legacy_parents: dict[str, str]) -> str:
    current = node_id
    seen: set[str] = set()
    while current and current not in seen:
        if current in valid_node_ids:
            return current
        seen.add(current)
        current = legacy_parents.get(current, "")
    return ""


def _filter_company_payload(
    payload: dict[str, Any], valid_node_ids: set[str], graph: dict[str, Any], legacy_parents: dict[str, str]
) -> tuple[dict[str, Any], int, int]:
    result = dict(payload)
    attachments = list(payload.get("attachments", []) or [])
    retained: list[dict[str, Any]] = []
    remapped = 0
    for item in attachments:
        original_node_id = str(item.get("node_id", ""))
        node_id = _nearest_surviving_node(original_node_id, valid_node_ids, legacy_parents)
        if not node_id:
            continue
        if node_id != original_node_id:
            item = {
                **item,
                "node_id": node_id,
                "reason": f"{item.get('reason', '').strip()}（原节点 {original_node_id} 已合并至现有父节点）",
            }
            remapped += 1
        retained.append(item)
    result["attachments"] = retained
    result["generated_at"] = now_iso()
    result["graph_fingerprint"] = company_graph_fingerprint(graph)
    return result, len(attachments) - len(retained), remapped


def _cross_branch_pair_count(catalog: list[dict[str, Any]]) -> int:
    by_branch = Counter(str(item.get("branch_id", "")) for item in catalog)
    total = len(catalog)
    return sum(count * (total - count) for count in by_branch.values()) // 2


def _retained_l2_inputs(
    pair_payload: dict[str, Any],
    relation_payload: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int | float], int]:
    """Retain only previously decided pairs whose two L2 endpoints still exist."""
    catalog_by_id = {str(item["id"]): item for item in catalog}
    valid_l2_ids = set(catalog_by_id)
    decision_by_id = {
        str(item.get("pair_id", "")): item
        for item in relation_payload.get("pair_decisions", []) or []
        if str(item.get("pair_id", ""))
        and str(item.get("node_a_id", "")) in valid_l2_ids
        and str(item.get("node_b_id", "")) in valid_l2_ids
    }
    original_pairs = list(pair_payload.get("pairs", []) or [])
    retained_pairs: list[dict[str, Any]] = []
    for pair in original_pairs:
        pair_id = str(pair.get("pair_id", ""))
        node_a_id = str(pair.get("node_a_id", ""))
        node_b_id = str(pair.get("node_b_id", ""))
        if pair_id not in decision_by_id or node_a_id not in valid_l2_ids or node_b_id not in valid_l2_ids:
            continue
        node_a, node_b = catalog_by_id[node_a_id], catalog_by_id[node_b_id]
        retained_pairs.append(
            {
                **pair,
                "node_a_name": node_a["name"],
                "node_b_name": node_b["name"],
                "node_a_branch": node_a["branch_name"],
                "node_b_branch": node_b["branch_name"],
            }
        )
    retained_pair_ids = {str(item["pair_id"]) for item in retained_pairs}
    retained_decisions = [
        decision_by_id[pair_id]
        for pair_id in sorted(retained_pair_ids)
    ]
    removed = len(original_pairs) - len(retained_pairs)
    audit_count = sum(
        "deterministic_negative_audit" in (item.get("selection_reasons") or [])
        for item in retained_pairs
    )
    summary: dict[str, int | float] = {
        "cross_branch_pair_count": _cross_branch_pair_count(catalog),
        "shortlisted_pair_count": len(retained_pairs) - audit_count,
        "negative_audit_pair_count": audit_count,
        "candidate_pair_count": len(retained_pairs),
        "candidates_per_node": pair_payload.get("summary", {}).get("candidates_per_node", 0),
        "negative_audit_rate": pair_payload.get("summary", {}).get("negative_audit_rate", 0.0),
    }
    return retained_pairs, retained_decisions, summary, removed


def _filter_cache_rows(rows: list[dict[str, Any]], valid_l2_ids: set[str]) -> tuple[list[dict[str, Any]], int]:
    retained = [
        row
        for row in rows
        if str(row.get("node_a_id", "")) in valid_l2_ids
        and str(row.get("node_b_id", "")) in valid_l2_ids
    ]
    return retained, len(rows) - len(retained)


def reconcile(industry_id: str) -> dict[str, Any]:
    directory = industry_dir(industry_id)
    graph = load_graph(industry_id)
    valid_node_ids = {str(node["id"]) for node in graph.get("nodes", []) if node.get("id")}
    legacy_graph_path = directory / "candidate_graph.json"
    legacy_parents: dict[str, str] = {}
    if legacy_graph_path.exists():
        legacy_graph = read_json(legacy_graph_path)
        legacy_parents = {
            str(node["id"]): str(node.get("parent_id") or "")
            for node in legacy_graph.get("nodes", [])
            if node.get("id")
        }
    result: dict[str, Any] = {"industry_id": industry_id, "node_count": len(valid_node_ids)}

    company_path = directory / "company_attachments.json"
    company_candidate_path = directory / "company_attachment_candidate.json"
    if company_path.exists():
        company_payload = read_json(company_path)
        reconciled_company, company_removed, company_remapped = _filter_company_payload(
            company_payload, valid_node_ids, graph, legacy_parents
        )
        company_report = validate_company_attachments(reconciled_company, graph, industry_id)
        if company_report["error_count"]:
            raise ValueError(f"公司挂载清理后校验失败：{company_report['issues']}")
        write_json(company_path, reconciled_company)
        write_json(directory / "company_attachment_validation_report.json", company_report)
        (directory / "company_attachment_validation_report.md").write_text(
            write_company_attachment_report(company_report), encoding="utf-8"
        )
        if company_candidate_path.exists():
            candidate_payload = read_json(company_candidate_path)
            reconciled_candidate, _, _ = _filter_company_payload(candidate_payload, valid_node_ids, graph, legacy_parents)
            write_json(company_candidate_path, reconciled_candidate)
        result["company_attachments"] = {
            "retained": len(reconciled_company.get("attachments", [])),
            "removed": company_removed,
            "remapped_to_surviving_parent": company_remapped,
            "validation_status": company_report["status"],
        }

    relation_path = directory / "l2_flow_relations.json"
    pair_path = directory / "l2_flow_candidate_pairs.json"
    cache_path = directory / "l2_flow_pair_decisions.jsonl"
    if relation_path.exists() and pair_path.exists():
        old_relations = read_json(relation_path)
        old_pairs = read_json(pair_path)
        catalog = compact_l2_catalog(graph)
        pairs, decisions, summary, pairs_removed = _retained_l2_inputs(old_pairs, old_relations, catalog)
        reconciled_relations = apply_l1_l2_projection(
            build_payload(industry_id, graph, catalog, pairs, decisions, summary), graph
        )
        relation_report = validate_l2_flow_relations(reconciled_relations, graph, industry_id)
        if relation_report["error_count"]:
            raise ValueError(f"L2 上下游关系清理后校验失败：{relation_report['issues']}")
        reconciled_pairs = {
            **old_pairs,
            "generated_at": now_iso(),
            "summary": summary,
            "pairs": pairs,
        }
        write_json(pair_path, reconciled_pairs)
        write_json(directory / "l2_flow_relation_candidate.json", reconciled_relations)
        write_json(relation_path, reconciled_relations)
        write_json(directory / "l2_flow_relation_validation_report.json", relation_report)
        (directory / "l2_flow_relation_validation_report.md").write_text(
            write_l2_flow_validation_report(relation_report), encoding="utf-8"
        )
        cache_removed = 0
        if cache_path.exists():
            cache_rows, cache_removed = _filter_cache_rows(read_jsonl(cache_path), {str(item["id"]) for item in catalog})
            write_jsonl(cache_path, cache_rows)
        result["l2_flow_relations"] = {
            "l2_nodes": len(catalog),
            "retained_pairs": len(pairs),
            "removed_pairs": pairs_removed,
            "retained_edges": len(reconciled_relations.get("edges", [])),
            "retained_projected_edges": len(reconciled_relations.get("projected_edges", [])),
            "removed_cached_decisions": cache_removed,
            "validation_status": relation_report["status"],
        }

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="在主图节点清理后本地对齐公司与 L2 上下游关系附件。")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    print(reconcile(args.industry_id))


if __name__ == "__main__":
    main()
