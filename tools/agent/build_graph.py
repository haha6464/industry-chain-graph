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

from tools.agent.common import industry_dir, standardize_graph, write_json, write_jsonl
from tools.agent.mergers.graph_merger import build_review_queue
from tools.agent.search.bailian_responses_agent import build_bailian_search_prompt, call_bailian_search_agent, evidence_from_agent_graph
from tools.agent.search.search_planner import build_search_plan
from tools.agent.validators.bailian_graph_validator import validate_and_repair_with_bailian
from tools.agent.validators.graph_validator import validate_graph, write_markdown_report
from tools.agent.export_csv import export_graph_csv


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def _write_build_report(
    path: Path,
    industry_name: str,
    evidence_count: int,
    validation: dict,
    semantic_validation: dict,
    review_queue: dict,
) -> None:
    lines = [
        f"# {industry_name} 构建报告",
        "",
        "## 运行摘要",
        "",
        "- 构建模式：bailian_search",
        f"- 证据数量：{evidence_count}",
        f"- 节点数：{validation['node_count']}",
        f"- 关系数：{validation['edge_count']}",
        f"- 硬规则校验状态：{validation['status']}",
        f"- 百炼校验状态：{semantic_validation.get('validation_status', 'unknown')}",
        f"- 百炼最小修改数：{len(semantic_validation.get('modifications', []))}",
        f"- 待复核项：{len(review_queue['items'])}",
        "",
        "## 说明",
        "",
        "本次构建通过阿里云百炼 Qwen Responses API 自动联网搜索并抽取候选节点和关系；随后由百炼校验 Agent 按最小修改原则修正图谱，再通过硬规则复检。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_graph(
    industry_id: str,
    industry_name: str | None,
    apply: bool = False,
    target_depth: str = "5-6 层，60-100 个节点，最多 150 个节点",
) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_industry_name = industry_name or industry_id
    _log(f"准备构建 {resolved_industry_name}，目标深度 {target_depth}。")

    _log("生成搜索计划 search_plan.json。")
    search_plan = build_search_plan(industry_id, resolved_industry_name)
    write_json(output_dir / "search_plan.json", search_plan)

    prompt_path = output_dir / "agent_request_prompt.txt"
    prompt_path.write_text(build_bailian_search_prompt(industry_id, resolved_industry_name, target_depth), encoding="utf-8")
    _log("请求百炼联网搜索与网页抽取，提示词已写入 agent_request_prompt.txt。")
    agent_graph, raw_text = call_bailian_search_agent(industry_id, resolved_industry_name, target_depth)
    raw_path = output_dir / "agent_raw_response.txt"
    raw_path.write_text(raw_text, encoding="utf-8")
    _log("百炼搜索响应已写入 agent_raw_response.txt。")

    _log("标准化候选图谱并写入 pre_validation_candidate_graph.json。")
    extracted_candidate = standardize_graph(agent_graph, industry_id)
    write_json(output_dir / "pre_validation_candidate_graph.json", extracted_candidate)

    _log("执行硬规则预校验。")
    pre_validation = validate_graph(extracted_candidate, industry_id)
    _log("请求百炼语义校验与最小修图，提示词将写入 validation_agent_request_prompt.txt。")
    corrected_candidate, semantic_validation, validation_raw_text = validate_and_repair_with_bailian(
        extracted_candidate,
        industry_id,
        pre_validation,
        output_dir / "validation_agent_request_prompt.txt",
    )
    validation_raw_path = output_dir / "validation_agent_raw_response.txt"
    validation_raw_path.write_text(validation_raw_text, encoding="utf-8")
    write_json(output_dir / "semantic_validation_report.json", semantic_validation)
    _log("百炼语义校验响应与报告已写入。")

    _log("生成最终候选图谱、证据库与复核队列。")
    candidate = standardize_graph(corrected_candidate, industry_id)
    evidence_rows = evidence_from_agent_graph(industry_id, candidate)
    write_jsonl(output_dir / "sources.jsonl", evidence_rows)

    candidate_path = output_dir / "candidate_graph.json"
    write_json(candidate_path, candidate)

    validation = validate_graph(candidate, industry_id)
    combined_validation = dict(validation)
    combined_validation["pre_validation"] = pre_validation
    combined_validation["semantic_validation"] = semantic_validation
    if semantic_validation.get("validation_status") == "fail":
        combined_validation["status"] = "fail"
    validation_path = output_dir / "validation_report.md"
    write_markdown_report(combined_validation, validation_path)
    write_json(validation_path.with_suffix(".json"), combined_validation)

    review_queue = build_review_queue(combined_validation, candidate.get("merge_report"))
    for item in semantic_validation.get("review_items", []):
        review_queue["items"].append({"type": "semantic_validation_issue", **item})
    review_queue["status"] = "pending_review" if review_queue["items"] else "clean"
    review_path = output_dir / "review_queue.json"
    write_json(review_path, review_queue)

    can_apply = validation["error_count"] == 0 and semantic_validation.get("validation_status") != "fail"
    if apply and can_apply:
        write_json(output_dir / "graph.json", candidate)
    _log("导出节点 CSV 与关系 CSV。")
    export = export_graph_csv(candidate, industry_id)

    build_report_path = output_dir / "build_report.md"
    _write_build_report(
        build_report_path,
        candidate.get("industry", resolved_industry_name),
        len(evidence_rows),
        validation,
        semantic_validation,
        review_queue,
    )
    _log("构建流程完成。")
    return {
        "industry_id": industry_id,
        "candidate_graph": str(candidate_path),
        "sources": str(output_dir / "sources.jsonl"),
        "validation_report": str(validation_path),
        "review_queue": str(review_path),
        "build_report": str(build_report_path),
        "agent_raw_response": str(raw_path),
        "validation_agent_raw_response": str(validation_raw_path),
        "semantic_validation_report": str(output_dir / "semantic_validation_report.json"),
        "pre_validation_candidate_graph": str(output_dir / "pre_validation_candidate_graph.json"),
        **export,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a candidate industry graph with Bailian Qwen web search.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--industry-name")
    parser.add_argument("--target-depth", default="5-6 层，60-100 个节点，最多 150 个节点")
    parser.add_argument("--apply", action="store_true", help="Overwrite graph.json only when validation has no errors.")
    args = parser.parse_args()
    result = build_graph(args.industry_id, args.industry_name, args.apply, args.target_depth)
    print(result)
