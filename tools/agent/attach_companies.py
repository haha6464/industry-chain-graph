from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, load_graph, load_manifest, save_manifest, write_json, write_jsonl
from tools.agent.l2_flow_relations import apply_l1_l2_projection, graph_fingerprint as l2_graph_fingerprint, relation_file_status
from tools.agent.company_attachments import (
    augment_scope_with_graph_taxonomy,
    build_attachment_payload,
    build_deterministic_taxonomy_matches,
    build_taxonomy_catalog,
    call_match_agent,
    call_scope_agent,
    collapse_company_attached_singleton_leaves,
    collapse_low_coverage_leaf_nodes,
    chunks,
    configured_batch_size,
    configured_max_concurrency,
    configured_model,
    configured_search_strategy,
    filter_domestic_listed_companies,
    filter_listed_attachments,
    load_candidate_companies,
    prune_graph_to_company_coverage,
    select_companies_by_scope,
)
from tools.agent.validators.bailian_company_attachment_validator import repair_company_attachments
from tools.agent.validators.company_attachment_validator import validate_company_attachments, write_company_attachment_report
from tools.agent.validators.graph_validator import validate_graph
from tools.agent.validators.l2_flow_relation_validator import validate_l2_flow_relations, write_l2_flow_validation_report


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def _progress_bar(current: int, total: int, elapsed_seconds: float | None = None, width: int = 28) -> str:
    if total <= 0:
        return "[----------------------------] 0/0 (0.0%)"
    ratio = min(1.0, current / total)
    filled = round(width * ratio)
    text = f"[{'=' * filled}{'-' * (width - filled)}] {current}/{total} ({ratio * 100:.1f}%)"
    if elapsed_seconds is None:
        return text
    text += f" · 已耗时 {_format_duration(elapsed_seconds)}"
    if current > 0:
        eta_seconds = elapsed_seconds / current * (total - current)
        text += f" · 预计剩余 {_format_duration(eta_seconds)}"
    else:
        text += " · 预计剩余：估算中"
    return text


def _write_validation(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "company_attachment_validation_report.md").write_text(
        write_company_attachment_report(report), encoding="utf-8"
    )
    write_json(output_dir / "company_attachment_validation_report.json", report)


def _rebase_l2_relations_after_pruning(payload: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """Drop L2 evidence for removed nodes, then deterministically rebuild projections."""
    node_by_id = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    l2_ids = {node_id for node_id, node in node_by_id.items() if int(node.get("level", -1)) == 2}
    decisions = [
        decision for decision in payload.get("pair_decisions", []) or []
        if str(decision.get("node_a_id")) in l2_ids and str(decision.get("node_b_id")) in l2_ids
    ]
    decision_ids = {str(decision.get("pair_id")) for decision in decisions}
    l2_edges = [
        edge for edge in payload.get("edges", []) or []
        if str(edge.get("source")) in l2_ids
        and str(edge.get("target")) in l2_ids
        and str(edge.get("decision_pair_id")) in decision_ids
    ]
    verdict_counts = Counter(str(decision.get("verdict", "")).upper() for decision in decisions)
    cache_hit_count = min(int((payload.get("candidate_summary") or {}).get("cache_hit_count", 0) or 0), len(decisions))
    rebased = {
        **payload,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "graph_fingerprint": l2_graph_fingerprint(graph),
        "evaluated_node_ids": sorted(l2_ids),
        "pair_decisions": decisions,
        "edges": l2_edges,
        "candidate_summary": {
            **(payload.get("candidate_summary") or {}),
            "candidate_pair_count": len(decisions),
            "pair_decision_count": len(decisions),
            "cache_hit_count": cache_hit_count,
            "model_decision_count": len(decisions) - cache_hit_count,
            "negative_audit_positive_count": sum(
                1 for decision in decisions
                if "deterministic_negative_audit" in (decision.get("selection_reasons") or [])
                and str(decision.get("verdict", "")).upper() in {"A_TO_B", "B_TO_A"}
            ),
            "verdict_counts": dict(sorted(verdict_counts.items())),
        },
    }
    return apply_l1_l2_projection(rebased, graph)


def _update_manifest_counts(industry_id: str, graph: dict[str, Any]) -> None:
    manifest = load_manifest()
    for item in manifest:
        if str(item.get("id")) == industry_id:
            item["node_count"] = len(graph.get("nodes", []))
            item["edge_count"] = len(graph.get("edges", []))
            break
    save_manifest(manifest)


def run_company_attachment(industry_id: str) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError("找不到正式 graph.json，请先完成最终校验并应用候选主图。")
    graph = load_graph(industry_id)
    relation_status, relation_payload, relation_message = relation_file_status(
        industry_id, graph, output_dir / "l2_flow_relations.json"
    )
    if relation_status != "ready" or relation_payload is None:
        raise FileNotFoundError(relation_message or "请先完成 L2 上下游关系建边。")
    all_companies = load_candidate_companies()
    companies = filter_domestic_listed_companies(all_companies)
    _log(f"已读取正式图谱和 {len(all_companies)} 家原始候选公司；预筛后保留 {len(companies)} 家境内上市公司。")
    if not companies:
        raise RuntimeError("候选公司中没有境内上市公司，无法进行公司节点挂载。")

    _log("生成基于 L0/L1 的申万分类范围规则。")
    scope, scope_prompt, scope_raw = call_scope_agent(graph, companies)
    scope = augment_scope_with_graph_taxonomy(scope, graph, build_taxonomy_catalog(companies))
    selected_companies = select_companies_by_scope(companies, scope)
    scope_payload = {
        **scope,
        "source_candidate_total_count": len(all_companies),
        "candidate_total_count": len(companies),
        "company_eligibility_rule": "is_listed is True (domestic listed company only)",
        "selected_company_count": len(selected_companies),
    }
    write_json(output_dir / "company_scope.json", scope_payload)
    (output_dir / "company_scope_request_prompt.txt").write_text(scope_prompt, encoding="utf-8")
    (output_dir / "company_scope_raw_response.txt").write_text(scope_raw, encoding="utf-8")
    if not selected_companies:
        raise RuntimeError("公司范围规划未筛出候选公司，请检查 company_scope.json 或重试。")
    _log(f"范围规划完成，筛出 {len(selected_companies)} 家候选公司。")

    batch_size = configured_batch_size()
    max_workers = configured_max_concurrency()
    batches = list(chunks(selected_companies, batch_size))
    _log(
        f"以公司为中心匹配：{len(batches)} 批，每批 {batch_size} 家，最大并发 {max_workers}，"
        f"模型 {configured_model()}，联网策略 {configured_search_strategy()}，思考模式关闭。"
    )
    _log("公司匹配进度 " + _progress_bar(0, len(batches)))
    raw_rows: list[dict[str, Any]] = []
    match_results = build_deterministic_taxonomy_matches(graph, selected_companies)
    deterministic_attachment_count = sum(len(item.get("matched_nodes", [])) for item in match_results)
    _log(f"精确分类兜底预挂载 {deterministic_attachment_count} 条；联网结果如命中更深节点将自动替代浅层挂载。")
    started_at = time.monotonic()

    def run_batch(index: int, batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], str, str]:
        results, prompt, raw = call_match_agent(graph, batch)
        return index, results, prompt, raw

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_batch, index, batch) for index, batch in enumerate(batches, start=1)]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            index, results, prompt, raw = future.result()
            match_results.extend(results)
            raw_rows.append(
                {
                    "batch_index": index,
                    "request_prompt": prompt,
                    "raw_response": raw,
                    "normalized_results": results,
                    "agent_omitted_company_ids": [
                        item["company_id"]
                        for item in results
                        if item.get("result_status") == "agent_omitted_filled_empty"
                    ],
                }
            )
            _log("公司匹配进度 " + _progress_bar(completed, len(batches), time.monotonic() - started_at))
    write_jsonl(output_dir / "company_attachment_raw_responses.jsonl", sorted(raw_rows, key=lambda item: item["batch_index"]))

    candidate = build_attachment_payload(industry_id, graph, all_companies, selected_companies, scope_payload, match_results)
    # The canvas and delivery both default to domestic listed companies.  Enforce
    # that same scope before coverage pruning so a non-visible company can never
    # keep an otherwise empty industry node in the formal graph.
    candidate, listed_stats = filter_listed_attachments(candidate)
    candidate_path = output_dir / "company_attachment_candidate.json"
    write_json(candidate_path, candidate)
    _log(
        f"已生成候选挂载：{len(candidate.get('companies', []))} 家境内上市公司、"
        f"{len(candidate.get('attachments', []))} 条直接挂载。"
    )
    if listed_stats["company_removed"] or listed_stats["attachment_removed"]:
        _log(
            f"已移除 {listed_stats['company_removed']} 家非境内上市公司及 "
            f"{listed_stats['attachment_removed']} 条对应挂载。"
        )

    validation = validate_company_attachments(candidate, graph, industry_id)
    repair_report: dict[str, Any] = {
        "validation_status": "skipped",
        "summary": "硬规则校验通过，未调用格式修复。",
        "modifications": [],
        "review_items": [],
    }
    if validation.get("error_count", 0) > 0:
        _log("公司挂载硬规则未通过，调用受限格式修复。")
        candidate, repair_report, repair_raw = repair_company_attachments(
            candidate, validation, output_dir / "company_attachment_validation_request_prompt.txt"
        )
        (output_dir / "company_attachment_validation_raw_response.txt").write_text(repair_raw, encoding="utf-8")
        write_json(candidate_path, candidate)
        validation = validate_company_attachments(candidate, graph, industry_id)
    else:
        (output_dir / "company_attachment_validation_request_prompt.txt").write_text(
            "硬规则校验通过，未调用格式修复。\n", encoding="utf-8"
        )
        (output_dir / "company_attachment_validation_raw_response.txt").write_text(
            "硬规则校验通过，未调用格式修复。\n", encoding="utf-8"
        )
    validation["format_repair"] = repair_report
    write_json(output_dir / "company_attachment_repair_report.json", repair_report)
    _write_validation(output_dir, validation)
    if validation.get("error_count", 0) > 0 or repair_report.get("validation_status") == "fail":
        raise RuntimeError(f"公司挂载硬规则校验失败：{validation.get('error_count', 0)} 个错误。")

    _log("按低覆盖阈值将公司向父级聚合，并裁剪无公司可聚合节点。")
    low_coverage_graph, candidate, low_coverage_removals = collapse_low_coverage_leaf_nodes(graph, candidate)
    pruned_graph, candidate, pruning_report = prune_graph_to_company_coverage(low_coverage_graph, candidate)
    pruning_report["low_coverage_leaf_compaction"] = {
        "threshold": 2,
        "rule": "非根叶节点直接挂载公司数小于或等于 2 家时，删除该节点并将公司向分类父节点聚合。",
        "removed_nodes": low_coverage_removals,
    }
    pruned_graph, candidate, singleton_removals = collapse_company_attached_singleton_leaves(pruned_graph, candidate)
    pruning_report["singleton_leaf_compaction"] = {
        "rule": "非根节点仅保留一个 contains 子节点时，删除子节点并将公司挂载上移到父节点。",
        "removed_nodes": singleton_removals,
    }
    if singleton_removals:
        _log(f"已压缩 {len(singleton_removals)} 个仅有唯一子分类的节点层级，并上移公司挂载。")
    if low_coverage_removals:
        _log(f"已按公司数阈值压缩 {len(low_coverage_removals)} 个低覆盖叶节点，并上移公司挂载。")
    graph_validation = validate_graph(pruned_graph, industry_id)
    if graph_validation.get("error_count", 0) > 0:
        raise RuntimeError(f"公司覆盖裁剪后的图谱硬规则校验失败：{graph_validation.get('error_count', 0)} 个错误。")
    rebased_l2_payload = _rebase_l2_relations_after_pruning(relation_payload, pruned_graph)
    l2_validation = validate_l2_flow_relations(rebased_l2_payload, pruned_graph, industry_id)
    if l2_validation.get("error_count", 0) > 0:
        raise RuntimeError(f"公司覆盖裁剪后的 L2 关系校验失败：{l2_validation.get('error_count', 0)} 个错误。")
    validation = validate_company_attachments(candidate, pruned_graph, industry_id)
    validation["format_repair"] = repair_report
    if validation.get("error_count", 0) > 0:
        raise RuntimeError(f"公司覆盖裁剪后的挂载校验失败：{validation.get('error_count', 0)} 个错误。")

    write_json(graph_path, pruned_graph)
    write_json(candidate_path, candidate)
    final_path = output_dir / "company_attachments.json"
    write_json(final_path, candidate)
    write_json(output_dir / "l2_flow_relations.json", rebased_l2_payload)
    (output_dir / "l2_flow_relation_validation_report.md").write_text(
        write_l2_flow_validation_report(l2_validation), encoding="utf-8"
    )
    write_json(output_dir / "l2_flow_relation_validation_report.json", l2_validation)
    pruning_report["graph_validation"] = graph_validation
    pruning_report["l2_relation_validation"] = l2_validation
    write_json(output_dir / "company_attachment_pruning_report.json", pruning_report)
    _write_validation(output_dir, validation)
    _update_manifest_counts(industry_id, pruned_graph)
    _log(
        f"公司节点挂载完成：图谱节点 {pruning_report['nodes_before']} → {pruning_report['nodes_after']}，"
        f"已删除 {len(pruning_report['removed_nodes'])} 个无公司可聚合节点，"
        f"并按阈值聚合 {len(low_coverage_removals)} 个低覆盖叶节点。"
    )
    return {
        "industry_id": industry_id,
        "company_scope": str(output_dir / "company_scope.json"),
        "company_attachment_candidate": str(candidate_path),
        "company_attachments": str(final_path),
        "company_attachment_pruning_report": str(output_dir / "company_attachment_pruning_report.json"),
        "validation_report": str(output_dir / "company_attachment_validation_report.md"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Attach candidate companies to a formal industry graph.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    result = run_company_attachment(args.industry_id)
    _log("完成：" + "、".join(path for key, path in result.items() if key != "industry_id"))
