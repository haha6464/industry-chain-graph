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
from typing import Any

from tools.agent.common import industry_dir, read_json, standardize_graph, write_json, write_jsonl
from tools.agent.export_csv import export_graph_csv
from tools.agent.mergers.graph_merger import build_review_queue
from tools.agent.search.bailian_responses_agent import evidence_from_agent_graph
from tools.agent.validators.bailian_graph_validator import validate_and_repair_with_bailian
from tools.agent.validators.graph_validator import validate_graph, write_markdown_report


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def _read_quality_opinions(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "staged_quality_opinions.json"
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def run_final_validation(industry_id: str, graph_file: Path | None = None) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = graph_file or output_dir / "pre_validation_candidate_graph.json"
    if not input_path.exists():
        raise FileNotFoundError(
            f"找不到校验前候选图谱：{input_path}。请先运行构建，至少生成 pre_validation_candidate_graph.json。"
        )

    _log(f"读取校验前候选图谱：{input_path}。")
    candidate = standardize_graph(read_json(input_path), industry_id)
    quality_opinions = _read_quality_opinions(output_dir)
    if quality_opinions:
        candidate["quality_evaluation"] = quality_opinions

    _log("执行单轮硬规则校验。")
    initial_validation = validate_graph(candidate, industry_id)
    format_repair_report: dict[str, Any] = {
        "validation_status": "skipped",
        "summary": "硬规则校验通过，未调用百炼格式修复。",
        "modifications": [],
        "review_items": [],
    }
    validation_raw_text = "硬规则校验通过，未调用百炼格式修复。\n"

    if initial_validation.get("error_count", 0) > 0:
        _log("硬规则校验未通过，请求百炼格式修复；提示词将写入 validation_agent_request_prompt.txt。")
        try:
            candidate, format_repair_report, validation_raw_text = validate_and_repair_with_bailian(
                candidate,
                industry_id,
                initial_validation,
                output_dir / "validation_agent_request_prompt.txt",
            )
        except Exception as exc:
            (output_dir / "agent_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            _log("百炼格式修复失败，错误已写入 agent_error.txt。")
            raise
    else:
        (output_dir / "validation_agent_request_prompt.txt").write_text("硬规则校验通过，未调用百炼格式修复。\n", encoding="utf-8")

    validation_raw_path = output_dir / "validation_agent_raw_response.txt"
    validation_raw_path.write_text(validation_raw_text, encoding="utf-8")
    write_json(output_dir / "format_repair_report.json", format_repair_report)

    _log("生成最终候选图谱、证据库、复核队列与 CSV。")
    candidate = standardize_graph(candidate, industry_id)
    if quality_opinions:
        candidate["quality_evaluation"] = quality_opinions
    candidate_path = output_dir / "candidate_graph.json"
    write_json(candidate_path, candidate)

    evidence_rows = evidence_from_agent_graph(industry_id, candidate)
    write_jsonl(output_dir / "sources.jsonl", evidence_rows)

    final_validation = validate_graph(candidate, industry_id)
    combined_validation = dict(final_validation)
    combined_validation["pre_validation"] = initial_validation
    combined_validation["format_repair"] = format_repair_report
    if quality_opinions:
        combined_validation["quality_evaluation"] = quality_opinions
    if format_repair_report.get("validation_status") == "fail":
        combined_validation["status"] = "fail"

    validation_path = output_dir / "validation_report.md"
    write_markdown_report(combined_validation, validation_path)
    write_json(validation_path.with_suffix(".json"), combined_validation)

    review_queue = build_review_queue(combined_validation, candidate.get("merge_report"))
    for item in format_repair_report.get("review_items", []):
        review_queue["items"].append({"type": "format_repair_issue", **item})
    for item in (quality_opinions or {}).get("items", []):
        if item.get("status") != "pass" or item.get("revised"):
            review_queue["items"].append({"type": "quality_evaluation_opinion", **item})
    review_queue["status"] = "pending_review" if review_queue["items"] else "clean"
    review_path = output_dir / "review_queue.json"
    write_json(review_path, review_queue)

    export = export_graph_csv(candidate, industry_id)
    _log("最终校验流程完成。")
    return {
        "industry_id": industry_id,
        "input_graph": str(input_path),
        "candidate_graph": str(candidate_path),
        "sources": str(output_dir / "sources.jsonl"),
        "validation_report": str(validation_path),
        "review_queue": str(review_path),
        "validation_agent_raw_response": str(validation_raw_path),
        "format_repair_report": str(output_dir / "format_repair_report.json"),
        **export,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run final hard-rule validation and optional Bailian format repair for an existing candidate graph.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--graph-file", type=Path)
    args = parser.parse_args()
    result = run_final_validation(args.industry_id, args.graph_file)
    print(result)

