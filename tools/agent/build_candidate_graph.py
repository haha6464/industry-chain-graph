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
import json
from typing import Any

from tools.agent.common import industry_dir, read_json, standardize_graph, write_json
from tools.agent.evaluators.bailian_quality_evaluator import (
    evaluate_branch_graph,
    evaluate_seed_graph,
    evaluation_passed,
    revise_branch_graph,
    revise_seed_graph,
)
from tools.agent.search.bailian_responses_agent import build_bailian_search_prompt, call_bailian_search_agent
from tools.agent.search.search_planner import build_search_plan
from tools.agent.search.staged_bailian_builder import (
    call_bailian_branch_graph,
    call_bailian_seed_graph,
    merge_staged_graphs,
    staged_branch_limit,
    write_staged_artifacts,
)


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def _quality_opinions(seed_record: dict[str, Any], branch_records: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    seed_eval = seed_record.get("evaluation") or {}
    items.append({
        "stage": "level1_skeleton",
        "status": seed_eval.get("status"),
        "score": seed_eval.get("score"),
        "summary": seed_eval.get("summary", ""),
        "opinions": seed_eval.get("opinions", []) or [],
        "revision_focus": seed_eval.get("revision_focus", []) or [],
        "revised": bool(seed_record.get("revised")),
    })
    for record in branch_records:
        branch_eval = record.get("evaluation") or {}
        items.append({
            "stage": "branch",
            "branch_id": record.get("branch_id"),
            "branch_name": record.get("branch_name"),
            "status": branch_eval.get("status"),
            "score": branch_eval.get("score"),
            "summary": branch_eval.get("summary", ""),
            "opinions": branch_eval.get("opinions", []) or [],
            "revision_focus": branch_eval.get("revision_focus", []) or [],
            "revised": bool(record.get("revised")),
        })
    return {"items": items}


def _load_seed_record(output_dir: Path) -> dict[str, Any]:
    record_path = output_dir / "staged_level1_evaluation.json"
    if record_path.exists():
        return read_json(record_path)
    graph_path = output_dir / "staged_level1_graph.json"
    if not graph_path.exists():
        raise FileNotFoundError("找不到 staged_level1_graph.json，请先运行一级骨架构建。")
    seed_graph = read_json(graph_path)
    return {"status": "ok", "evaluation": {}, "revised": False, "graph": seed_graph}


def build_level1_skeleton(
    industry_id: str,
    industry_name: str | None,
    target_depth: str = "5-6 层，60-100 个节点，最多 150 个节点",
) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in [
        "pre_validation_candidate_graph.json",
        "candidate_graph.json",
        "sources.jsonl",
        "staged_branch_fragments.json",
        "staged_branch_evaluations.json",
        "staged_merged_graph.json",
        "validation_report.md",
        "validation_report.json",
        "format_repair_report.json",
        "review_queue.json",
        "build_report.md",
        "semantic_validation_report.json",
    ]:
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()
    resolved_industry_name = industry_name or industry_id
    _log(f"准备构建一级骨架：{resolved_industry_name}，目标 {target_depth}。")

    _log("生成搜索计划 search_plan.json。")
    search_plan = build_search_plan(industry_id, resolved_industry_name)
    write_json(output_dir / "search_plan.json", search_plan)

    try:
        seed_graph, seed_raw_text, seed_prompt = call_bailian_seed_graph(industry_id, resolved_industry_name, target_depth)
    except Exception as exc:
        (output_dir / "agent_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        _log("百炼一级骨架构建失败，错误已写入 agent_error.txt。")
        raise

    (output_dir / "agent_request_prompt.txt").write_text(seed_prompt, encoding="utf-8")
    seed_graph = standardize_graph(seed_graph, industry_id)

    _log("评估一级骨架分类质量。")
    seed_evaluation, seed_eval_raw, seed_eval_prompt = evaluate_seed_graph(resolved_industry_name, seed_graph)
    seed_record: dict[str, Any] = {
        "status": "ok",
        "prompt": seed_prompt,
        "raw_response": seed_raw_text,
        "evaluation_prompt": seed_eval_prompt,
        "evaluation_raw_response": seed_eval_raw,
        "evaluation": seed_evaluation,
        "revised": False,
        "graph": seed_graph,
    }
    if not evaluation_passed(seed_evaluation):
        _log("一级骨架评估未通过，按评估意见请求修正骨架。")
        revised_seed, revise_raw, revise_prompt = revise_seed_graph(industry_id, resolved_industry_name, seed_graph, seed_evaluation)
        seed_graph = revised_seed
        seed_record.update({
            "revised": True,
            "revision_prompt": revise_prompt,
            "revision_raw_response": revise_raw,
            "graph": seed_graph,
        })
    else:
        _log("一级骨架评估通过，保留意见但不请求修正。")

    write_json(output_dir / "staged_level1_graph.json", seed_graph)
    write_json(output_dir / "staged_level1_evaluation.json", seed_record)
    write_json(output_dir / "staged_branch_fragments.json", {"items": []})
    write_json(output_dir / "staged_branch_evaluations.json", {"items": []})
    write_json(output_dir / "staged_quality_opinions.json", _quality_opinions(seed_record, []))
    write_json(output_dir / "staged_errors.json", {"items": []})
    (output_dir / "agent_raw_response.txt").write_text(json.dumps({"seed": seed_record, "branches": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("一级骨架构建与评估完成；后续可运行分支扩展。")
    return {
        "industry_id": industry_id,
        "search_plan": str(output_dir / "search_plan.json"),
        "staged_level1_graph": str(output_dir / "staged_level1_graph.json"),
        "staged_level1_evaluation": str(output_dir / "staged_level1_evaluation.json"),
        "staged_quality_opinions": str(output_dir / "staged_quality_opinions.json"),
        "agent_raw_response": str(output_dir / "agent_raw_response.txt"),
    }


def build_branch_candidates(
    industry_id: str,
    industry_name: str | None,
    target_depth: str = "5-6 层，60-100 个节点，最多 150 个节点",
) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in [
        "pre_validation_candidate_graph.json",
        "candidate_graph.json",
        "sources.jsonl",
        "staged_branch_fragments.json",
        "staged_branch_evaluations.json",
        "staged_merged_graph.json",
        "validation_report.md",
        "validation_report.json",
        "format_repair_report.json",
        "review_queue.json",
        "build_report.md",
        "semantic_validation_report.json",
    ]:
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()
    resolved_industry_name = industry_name or industry_id
    seed_record = _load_seed_record(output_dir)
    seed_graph = standardize_graph(seed_record.get("graph") or read_json(output_dir / "staged_level1_graph.json"), industry_id)
    _log(f"准备基于一级骨架扩展分支：{resolved_industry_name}。")

    level_one_nodes = [node for node in seed_graph.get("nodes", []) if int(node.get("level", 0)) == 1]
    branch_limit = staged_branch_limit()
    _log(f"发现 {len(level_one_nodes)} 个一级分支，最多扩展 {branch_limit} 个。")

    branch_graphs = []
    branch_records: list[dict[str, Any]] = []
    staged_errors = []
    for index, branch_node in enumerate(level_one_nodes[:branch_limit], start=1):
        branch_name = branch_node.get("name", branch_node.get("id", ""))
        _log(f"扩展分支 {index}/{min(len(level_one_nodes), branch_limit)}：{branch_name}。")
        try:
            branch_graph, branch_raw_text, branch_prompt = call_bailian_branch_graph(
                industry_id,
                resolved_industry_name,
                target_depth,
                seed_graph,
                branch_node,
            )
            branch_graph = standardize_graph(branch_graph, industry_id)
            _log(f"评估分支 {branch_name} 分类质量。")
            branch_evaluation, branch_eval_raw, branch_eval_prompt = evaluate_branch_graph(
                resolved_industry_name,
                seed_graph,
                branch_node,
                branch_graph,
            )
            branch_record: dict[str, Any] = {
                "branch_id": branch_node.get("id"),
                "branch_name": branch_name,
                "status": "ok",
                "prompt": branch_prompt,
                "raw_response": branch_raw_text,
                "evaluation_prompt": branch_eval_prompt,
                "evaluation_raw_response": branch_eval_raw,
                "evaluation": branch_evaluation,
                "revised": False,
                "graph": branch_graph,
            }
            if not evaluation_passed(branch_evaluation):
                _log(f"分支 {branch_name} 评估未通过，按意见请求修正该分支。")
                revised_branch, revise_raw, revise_prompt = revise_branch_graph(
                    industry_id,
                    resolved_industry_name,
                    branch_node,
                    branch_graph,
                    branch_evaluation,
                )
                branch_graph = revised_branch
                branch_record.update({
                    "revised": True,
                    "revision_prompt": revise_prompt,
                    "revision_raw_response": revise_raw,
                    "graph": branch_graph,
                })
            else:
                _log(f"分支 {branch_name} 评估通过，保留意见但不请求修正。")
            branch_graphs.append(branch_graph)
            branch_records.append(branch_record)
            _log(f"分支 {branch_name} 完成，候选节点 {len(branch_graph.get('nodes', []))} 个。")
        except Exception as exc:
            error = {"branch_id": branch_node.get("id"), "branch_name": branch_name, "error": f"{type(exc).__name__}: {exc}"}
            staged_errors.append(error)
            branch_records.append({"branch_id": branch_node.get("id"), "branch_name": branch_name, "status": "failed", "error": error["error"]})
            _log(f"分支 {branch_name} 扩展或评估失败，已记录后继续其他分支。")

    extracted_candidate = merge_staged_graphs(industry_id, resolved_industry_name, seed_graph, branch_graphs)
    quality_opinions = _quality_opinions(seed_record, branch_records)
    extracted_candidate["quality_evaluation"] = quality_opinions
    write_staged_artifacts(output_dir, seed_graph, branch_records, extracted_candidate, staged_errors)
    write_json(output_dir / "staged_branch_evaluations.json", {"items": branch_records})
    write_json(output_dir / "staged_quality_opinions.json", quality_opinions)
    (output_dir / "agent_raw_response.txt").write_text(json.dumps({"seed": seed_record, "branches": branch_records}, ensure_ascii=False, indent=2), encoding="utf-8")
    if staged_errors:
        (output_dir / "agent_error.txt").write_text(json.dumps({"staged_errors": staged_errors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _log("标准化合并图谱并写入 pre_validation_candidate_graph.json。")
    candidate = standardize_graph(extracted_candidate, industry_id)
    candidate["quality_evaluation"] = quality_opinions
    pre_validation_path = output_dir / "pre_validation_candidate_graph.json"
    write_json(pre_validation_path, candidate)
    _log("分支扩展与评估完成；后续请运行最终硬规则校验。")
    return {
        "industry_id": industry_id,
        "pre_validation_candidate_graph": str(pre_validation_path),
        "staged_branch_fragments": str(output_dir / "staged_branch_fragments.json"),
        "staged_branch_evaluations": str(output_dir / "staged_branch_evaluations.json"),
        "staged_quality_opinions": str(output_dir / "staged_quality_opinions.json"),
        "staged_merged_graph": str(output_dir / "staged_merged_graph.json"),
        "staged_errors": str(output_dir / "staged_errors.json"),
        "agent_raw_response": str(output_dir / "agent_raw_response.txt"),
    }


def build_pre_validation_candidate(
    industry_id: str,
    industry_name: str | None,
    target_depth: str = "5-6 层，60-100 个节点，最多 150 个节点",
    strategy: str = "staged",
) -> dict[str, str]:
    if strategy == "single":
        output_dir = industry_dir(industry_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        resolved_industry_name = industry_name or industry_id
        _log(f"准备 single 策略构建校验前候选图谱：{resolved_industry_name}。")
        search_plan = build_search_plan(industry_id, resolved_industry_name)
        write_json(output_dir / "search_plan.json", search_plan)
        prompt = build_bailian_search_prompt(industry_id, resolved_industry_name, target_depth)
        (output_dir / "agent_request_prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            agent_graph, raw_text = call_bailian_search_agent(industry_id, resolved_industry_name, target_depth)
        except Exception as exc:
            (output_dir / "agent_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            _log("百炼搜索构建失败，错误已写入 agent_error.txt。")
            raise
        (output_dir / "agent_raw_response.txt").write_text(raw_text, encoding="utf-8")
        candidate = standardize_graph(agent_graph, industry_id)
        write_json(output_dir / "staged_quality_opinions.json", {"items": []})
        pre_validation_path = output_dir / "pre_validation_candidate_graph.json"
        write_json(pre_validation_path, candidate)
        return {"industry_id": industry_id, "pre_validation_candidate_graph": str(pre_validation_path)}

    result = build_level1_skeleton(industry_id, industry_name, target_depth)
    result.update(build_branch_candidates(industry_id, industry_name, target_depth))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build staged industry graph candidates with separate skeleton and branch stages.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--industry-name")
    parser.add_argument("--target-depth", default="5-6 层，60-100 个节点，最多 150 个节点")
    parser.add_argument("--strategy", choices=["staged", "single"], default="staged")
    parser.add_argument("--stage", choices=["all", "skeleton", "branches"], default="all")
    args = parser.parse_args()
    if args.stage == "skeleton":
        result = build_level1_skeleton(args.industry_id, args.industry_name, args.target_depth)
    elif args.stage == "branches":
        result = build_branch_candidates(args.industry_id, args.industry_name, args.target_depth)
    else:
        result = build_pre_validation_candidate(args.industry_id, args.industry_name, args.target_depth, args.strategy)
    print(result)



