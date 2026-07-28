from __future__ import annotations

import argparse
import concurrent.futures
import sys
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, load_graph, write_json, write_jsonl
from tools.agent.l2_flow_relations import (
    PAIR_VERDICTS,
    build_candidate_pairs,
    build_payload,
    call_pair_batch,
    chunks,
    compact_l2_catalog,
    configured_candidates_per_node,
    configured_max_concurrency,
    configured_model,
    configured_negative_audit_rate,
    configured_pair_batch_size,
    configured_temperature,
    decision_cache_key,
    load_pair_decision_cache,
    now_iso,
)
from tools.agent.validators.l2_flow_relation_validator import (
    validate_l2_flow_relations,
    write_l2_flow_validation_report,
)


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def _write_validation(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "l2_flow_relation_validation_report.md").write_text(
        write_l2_flow_validation_report(report), encoding="utf-8"
    )
    write_json(output_dir / "l2_flow_relation_validation_report.json", report)


def run_l2_flow_relation_build(industry_id: str) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError("找不到正式 graph.json，请先完成最终校验并应用候选主图。")
    graph = load_graph(industry_id)
    catalog = compact_l2_catalog(graph)
    if not catalog:
        raise RuntimeError("正式图谱中没有 level=2 节点，无法运行 L2 上下游关系建边。")
    catalog_by_id = {str(node["id"]): node for node in catalog}

    candidate_pairs, candidate_summary = build_candidate_pairs(graph, catalog)
    pair_path = output_dir / "l2_flow_candidate_pairs.json"
    write_json(
        pair_path,
        {
            "industry_id": industry_id,
            "generated_at": now_iso(),
            "model": configured_model(),
            "temperature": configured_temperature(),
            "summary": candidate_summary,
            "pairs": candidate_pairs,
        },
    )
    if not candidate_pairs:
        raise RuntimeError("跨分支候选 L2 节点对为空，无法运行 pair 判定。")

    cache_path = output_dir / "l2_flow_pair_decisions.jsonl"
    cache = load_pair_decision_cache(cache_path)
    pair_by_id = {str(pair["pair_id"]): pair for pair in candidate_pairs}
    pair_decisions: dict[str, dict[str, Any]] = {}
    pending_pairs: list[dict[str, Any]] = []
    for pair in candidate_pairs:
        cache_key = decision_cache_key(pair, catalog_by_id)
        cached = cache.get(cache_key)
        if (
            cached
            and cached.get("pair_id") == pair["pair_id"]
            and cached.get("verdict") in PAIR_VERDICTS
        ):
            pair_decisions[pair["pair_id"]] = {
                "pair_id": pair["pair_id"],
                "verdict": cached["verdict"],
                "decision_source": "cache",
                "cache_key": cache_key,
            }
        else:
            pending = dict(pair)
            pending["cache_key"] = cache_key
            pending_pairs.append(pending)

    pair_batch_size = configured_pair_batch_size()
    max_workers = configured_max_concurrency()
    batches = list(chunks(pending_pairs, pair_batch_size))
    _log(
        f"L2 pairwise 建边：{len(catalog)} 个 L2，跨分支 {candidate_summary['cross_branch_pair_count']} 对，"
        f"候选 {len(candidate_pairs)} 对，缓存命中 {len(pair_decisions)} 对，待判定 {len(pending_pairs)} 对。"
    )
    _log(
        f"模型 {configured_model()}，temperature={configured_temperature()}，每请求 {pair_batch_size} 对，"
        f"最大并发 {max_workers}；联网关闭、思考关闭、工具关闭。"
    )
    raw_rows: list[dict[str, Any]] = []
    missing_after_batch: list[dict[str, Any]] = []

    def run_batch(index: int, batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], dict[str, str], str, str]:
        verdicts, prompt, raw = call_pair_batch(catalog_by_id, batch)
        return index, batch, verdicts, prompt, raw

    if batches:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(run_batch, index, batch) for index, batch in enumerate(batches, start=1)]
            for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                index, batch, verdicts, prompt, raw = future.result()
                raw_rows.append(
                    {
                        "stage": "pair_batch",
                        "batch_index": index,
                        "pair_ids": [pair["pair_id"] for pair in batch],
                        "request_prompt": prompt,
                        "raw_response": raw,
                        "parsed_verdicts": verdicts,
                    }
                )
                for pair in batch:
                    verdict = verdicts.get(pair["pair_id"])
                    if verdict in PAIR_VERDICTS:
                        pair_decisions[pair["pair_id"]] = {
                            "pair_id": pair["pair_id"],
                            "verdict": verdict,
                            "decision_source": "model_batch",
                            "cache_key": pair["cache_key"],
                        }
                    else:
                        missing_after_batch.append(pair)
                _log(f"pair 批处理进度 {completed}/{len(batches)}。")

    if missing_after_batch:
        _log(f"有 {len(missing_after_batch)} 个 pair 返回缺失或格式非法，改为逐 pair 重试。")

        def retry_pair(pair: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], str, str]:
            verdicts, prompt, raw = call_pair_batch(catalog_by_id, [pair])
            return pair, verdicts, prompt, raw

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(retry_pair, pair) for pair in missing_after_batch]
            for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                pair, verdicts, prompt, raw = future.result()
                verdict = verdicts.get(pair["pair_id"])
                raw_rows.append(
                    {
                        "stage": "single_pair_retry",
                        "retry_index": completed,
                        "pair_ids": [pair["pair_id"]],
                        "request_prompt": prompt,
                        "raw_response": raw,
                        "parsed_verdicts": verdicts,
                    }
                )
                pair_decisions[pair["pair_id"]] = {
                    "pair_id": pair["pair_id"],
                    "verdict": verdict if verdict in PAIR_VERDICTS else "INVALID",
                    "decision_source": "model_single_retry" if verdict in PAIR_VERDICTS else "invalid_after_retry",
                    "cache_key": pair["cache_key"],
                }

    write_jsonl(
        output_dir / "l2_flow_relation_raw_responses.jsonl",
        sorted(raw_rows, key=lambda item: (item["stage"], int(item.get("batch_index", item.get("retry_index", 0))))),
    )

    updated_cache = dict(cache)
    for decision in pair_decisions.values():
        if decision.get("verdict") not in PAIR_VERDICTS:
            continue
        pair = pair_by_id[decision["pair_id"]]
        updated_cache[decision["cache_key"]] = {
            "cache_key": decision["cache_key"],
            "pair_id": decision["pair_id"],
            "node_a_id": pair["node_a_id"],
            "node_b_id": pair["node_b_id"],
            "verdict": decision["verdict"],
            "model": configured_model(),
            "temperature": configured_temperature(),
            "updated_at": now_iso(),
        }
    write_jsonl(cache_path, sorted(updated_cache.values(), key=lambda item: str(item.get("cache_key", ""))))

    candidate = build_payload(
        industry_id,
        graph,
        catalog,
        candidate_pairs,
        list(pair_decisions.values()),
        candidate_summary,
    )
    candidate_path = output_dir / "l2_flow_relation_candidate.json"
    write_json(candidate_path, candidate)
    validation = validate_l2_flow_relations(candidate, graph, industry_id)
    _write_validation(output_dir, validation)
    if validation.get("error_count", 0) > 0:
        raise RuntimeError(f"L2 上下游关系硬规则校验失败：{validation.get('error_count', 0)} 个错误。")

    final_path = output_dir / "l2_flow_relations.json"
    write_json(final_path, candidate)
    _log(
        f"L2 pairwise 建边完成：判定 {validation.get('pair_decision_count', 0)} 对，"
        f"发布 {validation.get('relation_count', 0)} 条关系，缓存命中 {candidate['candidate_summary']['cache_hit_count']} 对。"
    )
    return {
        "industry_id": industry_id,
        "candidate_pairs": str(pair_path),
        "candidate": str(candidate_path),
        "relations": str(final_path),
        "decision_cache": str(cache_path),
        "validation_report": str(output_dir / "l2_flow_relation_validation_report.md"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build pairwise level-2 upstream/downstream relation overlays.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    result = run_l2_flow_relation_build(args.industry_id)
    _log("完成：" + "、".join(path for key, path in result.items() if key != "industry_id"))
