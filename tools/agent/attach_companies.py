from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import industry_dir, load_graph, write_json, write_jsonl
from tools.agent.company_attachments import (
    build_attachment_payload,
    call_match_agent,
    call_scope_agent,
    chunks,
    configured_batch_size,
    configured_max_concurrency,
    configured_model,
    configured_search_strategy,
    load_candidate_companies,
    select_companies_by_scope,
)
from tools.agent.validators.bailian_company_attachment_validator import repair_company_attachments
from tools.agent.validators.company_attachment_validator import validate_company_attachments, write_company_attachment_report


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


def run_company_attachment(industry_id: str) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError("找不到正式 graph.json，请先完成最终校验并应用候选主图。")
    graph = load_graph(industry_id)
    companies = load_candidate_companies()
    _log(f"已读取正式图谱和 {len(companies)} 家候选公司。")

    _log("生成基于 L0/L1 的申万分类范围规则。")
    scope, scope_prompt, scope_raw = call_scope_agent(graph, companies)
    selected_companies = select_companies_by_scope(companies, scope)
    scope_payload = {
        **scope,
        "candidate_total_count": len(companies),
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
    match_results: list[dict[str, Any]] = []
    started_at = time.monotonic()

    def run_batch(index: int, batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], str, str]:
        results, prompt, raw = call_match_agent(graph, batch)
        return index, results, prompt, raw

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_batch, index, batch) for index, batch in enumerate(batches, start=1)]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            index, results, prompt, raw = future.result()
            match_results.extend(results)
            raw_rows.append({"batch_index": index, "request_prompt": prompt, "raw_response": raw})
            _log("公司匹配进度 " + _progress_bar(completed, len(batches), time.monotonic() - started_at))
    write_jsonl(output_dir / "company_attachment_raw_responses.jsonl", sorted(raw_rows, key=lambda item: item["batch_index"]))

    candidate = build_attachment_payload(industry_id, graph, companies, selected_companies, scope_payload, match_results)
    candidate_path = output_dir / "company_attachment_candidate.json"
    write_json(candidate_path, candidate)
    _log(f"已生成候选挂载：{len(candidate.get('companies', []))} 家公司、{len(candidate.get('attachments', []))} 条直接挂载。")

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

    final_path = output_dir / "company_attachments.json"
    write_json(final_path, candidate)
    _log("公司节点挂载完成并已发布独立附件。")
    return {
        "industry_id": industry_id,
        "company_scope": str(output_dir / "company_scope.json"),
        "company_attachment_candidate": str(candidate_path),
        "company_attachments": str(final_path),
        "validation_report": str(output_dir / "company_attachment_validation_report.md"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Attach candidate companies to a formal industry graph.")
    parser.add_argument("--industry-id", required=True)
    args = parser.parse_args()
    result = run_company_attachment(args.industry_id)
    _log("完成：" + "、".join(path for key, path in result.items() if key != "industry_id"))
