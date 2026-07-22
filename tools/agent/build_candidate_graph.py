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
    call_bailian_seed_blueprint,
    call_bailian_seed_graph,
    merge_staged_graphs,
    namespace_branch_graph,
    staged_branch_limit,
    validate_branch_expansion,
    write_staged_artifacts,
)


def _log(message: str) -> None:
    print(f"[agent] {message}", flush=True)

_ARTIFACT_LABELS = {
    "search_plan": "搜索计划",
    "staged_level1_blueprint": "一级分类蓝图",
    "staged_level1_graph": "一级骨架",
    "staged_level1_evaluation": "骨架评估",
    "staged_quality_opinions": "质量意见",
    "agent_raw_response": "原始响应",
    "pre_validation_candidate_graph": "校验前候选图谱",
    "staged_branch_fragments": "分支片段",
    "staged_branch_evaluations": "分支评估",
    "staged_merged_graph": "分阶段合并图谱",
    "staged_errors": "分支错误记录",
}


def _log_result_summary(result: dict[str, Any]) -> None:
    artifact_labels = [
        _ARTIFACT_LABELS.get(key, key)
        for key, value in result.items()
        if key != "industry_id" and value
    ]
    _log(f"完成：{result.get('industry_id', '')}。")
    if artifact_labels:
        _log("已生成产物：" + "、".join(artifact_labels) + "。")

def _quality_opinion_item(stage: str, evaluation: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    initial_status = evaluation.get("status") or "unknown"
    revised = bool(record.get("revised"))
    item = {
        "stage": stage,
        "status": "auto_revised" if revised else initial_status,
        "initial_status": initial_status,
        "revision_status": "auto_revised_not_rechecked" if revised else "not_revised",
        "score": evaluation.get("score"),
        "initial_score": evaluation.get("score"),
        "summary": evaluation.get("summary", ""),
        "opinions": evaluation.get("opinions", []) or [],
        "revision_focus": evaluation.get("revision_focus", []) or [],
        "revised": revised,
    }
    if revised:
        post_evaluation = record.get("post_revision_evaluation") or {}
        if post_evaluation:
            item["revision_status"] = "rechecked_pass" if evaluation_passed(post_evaluation) else "rechecked_needs_review"
            item["final_score"] = post_evaluation.get("score")
            item["final_summary"] = post_evaluation.get("summary", "")
            item["final_opinions"] = post_evaluation.get("opinions", []) or []
            item["final_revision_focus"] = post_evaluation.get("revision_focus", []) or []
            item["revision_note"] = "已按初评意见自动修正并完成一次复评。"
        else:
            item["revision_note"] = "已按初评意见自动修正，但未再次请求 LLM 打分；请以后续最终图谱和人工复核为准。"
    return item


def _quality_opinions(seed_record: dict[str, Any], branch_records: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    seed_eval = seed_record.get("evaluation") or {}
    items.append(_quality_opinion_item("level1_skeleton", seed_eval, seed_record))
    for record in branch_records:
        branch_eval = record.get("evaluation") or {}
        item = _quality_opinion_item("branch", branch_eval, record)
        item.update({
            "branch_id": record.get("branch_id"),
            "branch_name": record.get("branch_name"),
        })
        items.append(item)
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
    target_depth: str = "L0-L4（5 层），节点通常在 120 个以上，不设硬上限，避免低价值概念堆节点",
) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in [
        "staged_level1_blueprint.json",
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
        _log("研究行业边界并设计一级分类蓝图。")
        seed_blueprint, blueprint_raw_text, blueprint_prompt = call_bailian_seed_blueprint(resolved_industry_name)
        write_json(output_dir / "staged_level1_blueprint.json", {
            "prompt": blueprint_prompt,
            "raw_response": blueprint_raw_text,
            "blueprint": seed_blueprint,
        })
        _log("根据一级分类蓝图构建骨架。")
        seed_graph, seed_raw_text, seed_prompt = call_bailian_seed_graph(
            industry_id,
            resolved_industry_name,
            target_depth,
            seed_blueprint,
        )
    except Exception as exc:
        (output_dir / "agent_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        _log("百炼一级骨架构建失败，错误已写入 agent_error.txt。")
        raise

    (output_dir / "agent_request_prompt.txt").write_text(seed_prompt, encoding="utf-8")
    seed_graph = standardize_graph(seed_graph, industry_id)

    _log("评估一级骨架分类质量。")
    seed_evaluation, seed_eval_raw, seed_eval_prompt = evaluate_seed_graph(
        resolved_industry_name,
        seed_graph,
        seed_blueprint,
    )
    seed_record: dict[str, Any] = {
        "status": "ok",
        "prompt": seed_prompt,
        "raw_response": seed_raw_text,
        "evaluation_prompt": seed_eval_prompt,
        "evaluation_raw_response": seed_eval_raw,
        "evaluation": seed_evaluation,
        "classification_blueprint": seed_blueprint,
        "revised": False,
        "graph": seed_graph,
    }
    if seed_evaluation.get("parse_error"):
        _log("一级骨架质量评估 JSON 解析失败，保留骨架并跳过自动修正。")
    elif not evaluation_passed(seed_evaluation):
        _log("一级骨架评估未通过，按评估意见请求修正骨架。")
        revised_seed, revise_raw, revise_prompt = revise_seed_graph(
            industry_id,
            resolved_industry_name,
            seed_graph,
            seed_evaluation,
            seed_blueprint,
        )
        seed_graph = revised_seed
        _log("复评修正后的一级骨架。")
        post_evaluation, post_eval_raw, post_eval_prompt = evaluate_seed_graph(
            resolved_industry_name,
            seed_graph,
            seed_blueprint,
        )
        seed_record.update({
            "revised": True,
            "revision_prompt": revise_prompt,
            "revision_raw_response": revise_raw,
            "post_revision_evaluation_prompt": post_eval_prompt,
            "post_revision_evaluation_raw_response": post_eval_raw,
            "post_revision_evaluation": post_evaluation,
            "graph": seed_graph,
        })
        if evaluation_passed(post_evaluation):
            _log("修正后的一级骨架复评通过。")
        else:
            _log("修正后的一级骨架复评仍未通过，保留意见供人工复核。")
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
        "staged_level1_blueprint": str(output_dir / "staged_level1_blueprint.json"),
        "staged_level1_graph": str(output_dir / "staged_level1_graph.json"),
        "staged_level1_evaluation": str(output_dir / "staged_level1_evaluation.json"),
        "staged_quality_opinions": str(output_dir / "staged_quality_opinions.json"),
        "agent_raw_response": str(output_dir / "agent_raw_response.txt"),
    }


def build_branch_candidates(
    industry_id: str,
    industry_name: str | None,
    target_depth: str = "L0-L4（5 层），节点通常在 120 个以上，不设硬上限，避免低价值概念堆节点",
) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_industry_name = industry_name or industry_id
    seed_record = _load_seed_record(output_dir)
    seed_graph = standardize_graph(seed_record.get("graph") or read_json(output_dir / "staged_level1_graph.json"), industry_id)
    seed_blueprint = seed_record.get("classification_blueprint") or {}
    effective_seed_evaluation = seed_record.get("post_revision_evaluation") or seed_record.get("evaluation") or {}
    if (
        effective_seed_evaluation
        and not effective_seed_evaluation.get("parse_error")
        and not evaluation_passed(effective_seed_evaluation)
    ):
        raise RuntimeError("一级骨架最终评估未通过，请先重新运行骨架阶段或人工调整后再扩展分支。")

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
    _log(f"准备基于一级骨架扩展分支：{resolved_industry_name}。")

    level_one_nodes = [node for node in seed_graph.get("nodes", []) if int(node.get("level", 0)) == 1]
    branch_limit = staged_branch_limit(len(level_one_nodes))
    if branch_limit >= len(level_one_nodes):
        _log(f"发现 {len(level_one_nodes)} 个一级分支，将全部扩展。")
    else:
        _log(f"发现 {len(level_one_nodes)} 个一级分支，按调试上限扩展 {branch_limit} 个。")

    branch_graphs = []
    branch_records: list[dict[str, Any]] = []
    staged_errors = []
    branches_to_expand = level_one_nodes[:branch_limit]
    for index, branch_node in enumerate(branches_to_expand, start=1):
        branch_name = branch_node.get("name", branch_node.get("id", ""))
        _log(f"扩展分支 {index}/{len(branches_to_expand)}：{branch_name}。")
        try:
            branch_graph, branch_raw_text, branch_prompt = call_bailian_branch_graph(
                industry_id,
                resolved_industry_name,
                target_depth,
                seed_graph,
                branch_node,
                seed_blueprint,
            )
            branch_graph = namespace_branch_graph(branch_graph, branch_node)
            branch_graph = standardize_graph(branch_graph, industry_id)
            validate_branch_expansion(branch_graph, branch_node, min_new_nodes=1)
            _log(f"评估分支 {branch_name} 分类质量。")
            branch_evaluation, branch_eval_raw, branch_eval_prompt = evaluate_branch_graph(
                resolved_industry_name,
                seed_graph,
                branch_node,
                branch_graph,
                seed_blueprint,
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
            if branch_evaluation.get("parse_error"):
                _log(f"分支 {branch_name} 质量评估 JSON 解析失败，保留当前分支并跳过自动修正。")
            elif not evaluation_passed(branch_evaluation):
                _log(f"分支 {branch_name} 评估未通过，按意见请求修正该分支。")
                revised_branch, revise_raw, revise_prompt = revise_branch_graph(
                    industry_id,
                    resolved_industry_name,
                    branch_node,
                    branch_graph,
                    branch_evaluation,
                    seed_blueprint,
                )
                branch_graph = namespace_branch_graph(revised_branch, branch_node)
                branch_graph = standardize_graph(branch_graph, industry_id)
                branch_record.update({
                    "revised": True,
                    "revision_prompt": revise_prompt,
                    "revision_raw_response": revise_raw,
                    "graph": branch_graph,
                })
            else:
                _log(f"分支 {branch_name} 评估通过，保留意见但不请求修正。")
            validate_branch_expansion(branch_graph, branch_node)
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
        raise RuntimeError(f"有 {len(staged_errors)} 个一级分支扩展失败，已保留分支产物但不生成校验前候选图谱。")

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


def rebuild_staged_candidate(industry_id: str, industry_name: str | None) -> dict[str, str]:
    output_dir = industry_dir(industry_id)
    resolved_industry_name = industry_name or industry_id
    seed_record = _load_seed_record(output_dir)
    seed_graph = standardize_graph(seed_record.get("graph") or read_json(output_dir / "staged_level1_graph.json"), industry_id)
    records_path = output_dir / "staged_branch_evaluations.json"
    if not records_path.exists():
        raise FileNotFoundError("找不到 staged_branch_evaluations.json，无法从已有分支产物重建候选图谱。")

    level_one_by_id = {
        str(node.get("id")): node
        for node in seed_graph.get("nodes", [])
        if int(node.get("level", 0)) == 1
    }
    branch_records = read_json(records_path).get("items", []) or []
    branch_graphs: list[dict[str, Any]] = []
    rebuild_errors: list[dict[str, Any]] = []
    for record in branch_records:
        if record.get("status") != "ok" or not record.get("graph"):
            rebuild_errors.append({
                "branch_id": record.get("branch_id"),
                "branch_name": record.get("branch_name"),
                "error": record.get("error") or "分支记录缺少可用图谱。",
            })
            continue
        branch_node = level_one_by_id.get(str(record.get("branch_id")))
        if branch_node is None:
            rebuild_errors.append({
                "branch_id": record.get("branch_id"),
                "branch_name": record.get("branch_name"),
                "error": "分支记录无法匹配当前一级骨架。",
            })
            continue
        try:
            branch_graph = namespace_branch_graph(record["graph"], branch_node)
            branch_graph = standardize_graph(branch_graph, industry_id)
            validate_branch_expansion(branch_graph, branch_node)
            record["graph"] = branch_graph
            branch_graphs.append(branch_graph)
        except Exception as exc:
            rebuild_errors.append({
                "branch_id": record.get("branch_id"),
                "branch_name": record.get("branch_name"),
                "error": f"{type(exc).__name__}: {exc}",
            })

    if rebuild_errors:
        write_json(output_dir / "staged_errors.json", {"items": rebuild_errors})
        raise RuntimeError(f"已有分支产物中有 {len(rebuild_errors)} 个分支无法重建。")

    merged = merge_staged_graphs(industry_id, resolved_industry_name, seed_graph, branch_graphs)
    quality_opinions = _quality_opinions(seed_record, branch_records)
    merged["quality_evaluation"] = quality_opinions
    write_staged_artifacts(output_dir, seed_graph, branch_records, merged, [])
    write_json(records_path, {"items": branch_records})
    write_json(output_dir / "staged_quality_opinions.json", quality_opinions)
    (output_dir / "agent_raw_response.txt").write_text(
        json.dumps({"seed": seed_record, "branches": branch_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    candidate = standardize_graph(merged, industry_id)
    candidate["quality_evaluation"] = quality_opinions
    pre_validation_path = output_dir / "pre_validation_candidate_graph.json"
    write_json(pre_validation_path, candidate)
    _log(f"已从现有分支产物重建候选图谱：{len(candidate.get('nodes', []))} 个节点。")
    return {
        "industry_id": industry_id,
        "pre_validation_candidate_graph": str(pre_validation_path),
        "staged_branch_fragments": str(output_dir / "staged_branch_fragments.json"),
        "staged_branch_evaluations": str(records_path),
        "staged_merged_graph": str(output_dir / "staged_merged_graph.json"),
    }


def build_pre_validation_candidate(
    industry_id: str,
    industry_name: str | None,
    target_depth: str = "L0-L4（5 层），节点通常在 120 个以上，不设硬上限，避免低价值概念堆节点",
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
    parser.add_argument("--target-depth", default="L0-L4（5 层），节点通常在 120 个以上，不设硬上限，避免低价值概念堆节点")
    parser.add_argument("--strategy", choices=["staged", "single"], default="staged")
    parser.add_argument("--stage", choices=["all", "skeleton", "branches", "rebuild"], default="all")
    args = parser.parse_args()
    if args.stage == "skeleton":
        result = build_level1_skeleton(args.industry_id, args.industry_name, args.target_depth)
    elif args.stage == "branches":
        result = build_branch_candidates(args.industry_id, args.industry_name, args.target_depth)
    elif args.stage == "rebuild":
        result = rebuild_staged_candidate(args.industry_id, args.industry_name)
    else:
        result = build_pre_validation_candidate(args.industry_id, args.industry_name, args.target_depth, args.strategy)
    _log_result_summary(result)










