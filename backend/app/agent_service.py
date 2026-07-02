from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.agent.common import industry_dir, read_json, read_jsonl, standardize_graph, write_json, write_jsonl
from tools.agent.export_csv import export_graph_csv, export_industry_csv
from tools.agent.search.search_planner import build_search_plan
from tools.agent.validators.graph_validator import validate_graph, write_markdown_report


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    label: str
    relative_path: str
    kind: str


ARTIFACT_SPECS = [
    ArtifactSpec("graph", "正式图谱", "graph.json", "json"),
    ArtifactSpec("candidate_graph", "候选图谱", "candidate_graph.json", "json"),
    ArtifactSpec("pre_validation_candidate_graph", "校验前候选图谱", "pre_validation_candidate_graph.json", "json"),
    ArtifactSpec("sources", "证据库", "sources.jsonl", "jsonl"),
    ArtifactSpec("search_plan", "搜索计划", "search_plan.json", "json"),
    ArtifactSpec("review_queue", "人工复核队列", "review_queue.json", "json"),
    ArtifactSpec("validation_report", "规则校验报告", "validation_report.md", "markdown"),
    ArtifactSpec("validation_report_json", "规则校验数据", "validation_report.json", "json"),
    ArtifactSpec("format_repair_report", "格式修复报告", "format_repair_report.json", "json"),
    ArtifactSpec("update_proposal", "更新提案", "update_proposal.json", "json"),
    ArtifactSpec("update_candidate_graph", "更新候选图谱", "update_candidate_graph.json", "json"),
    ArtifactSpec("update_report", "更新报告", "update_report.md", "markdown"),
    ArtifactSpec("agent_request_prompt", "构建 Agent 请求提示词", "agent_request_prompt.txt", "text"),
    ArtifactSpec("agent_raw_response", "构建 Agent 原始响应", "agent_raw_response.txt", "text"),
    ArtifactSpec("staged_level1_graph", "分阶段一级骨架", "staged_level1_graph.json", "json"),
    ArtifactSpec("staged_level1_evaluation", "一级骨架质量评估", "staged_level1_evaluation.json", "json"),
    ArtifactSpec("staged_branch_fragments", "分阶段分支扩展", "staged_branch_fragments.json", "json"),
    ArtifactSpec("staged_branch_evaluations", "分支质量评估", "staged_branch_evaluations.json", "json"),
    ArtifactSpec("staged_quality_opinions", "合并质量意见", "staged_quality_opinions.json", "json"),
    ArtifactSpec("staged_merged_graph", "分阶段合并图谱", "staged_merged_graph.json", "json"),
    ArtifactSpec("staged_errors", "分阶段失败记录", "staged_errors.json", "json"),
    ArtifactSpec("agent_error", "构建 Agent 失败信息", "agent_error.txt", "text"),
    ArtifactSpec("validation_agent_request_prompt", "校验 Agent 请求提示词", "validation_agent_request_prompt.txt", "text"),
    ArtifactSpec("validation_agent_raw_response", "校验 Agent 原始响应", "validation_agent_raw_response.txt", "text"),
    ArtifactSpec("update_agent_request_prompt", "更新 Agent 请求提示词", "update_agent_request_prompt.txt", "text"),
    ArtifactSpec("update_agent_raw_response", "更新 Agent 原始响应", "update_agent_raw_response.txt", "text"),
    ArtifactSpec("update_agent_error", "更新 Agent 失败信息", "update_agent_error.txt", "text"),
]
ARTIFACT_BY_NAME = {spec.name: spec for spec in ARTIFACT_SPECS}


@dataclass
class AgentRunState:
    run_id: str
    industry_id: str
    kind: str
    status: str = "running"
    current_step: str = "准备运行"
    report_path: str | None = None
    command: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    ended_at: str | None = None
    returncode: int | None = None
    process: subprocess.Popen[str] | None = None


RUNS: dict[str, AgentRunState] = {}
RUN_LOCK = threading.Lock()
MAX_LOG_LINES = 500


def _artifact_path(industry_id: str, spec: ArtifactSpec) -> Path:
    base = industry_dir(industry_id).resolve()
    path = (base / spec.relative_path).resolve()
    if base not in path.parents and path != base:
        raise FileNotFoundError(f"Invalid artifact path: {spec.name}")
    return path


def _append_log(state: AgentRunState, message: str) -> None:
    clean_message = message.rstrip()
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {clean_message}"
    with RUN_LOCK:
        state.logs.append(line)
        if len(state.logs) > MAX_LOG_LINES:
            state.logs = state.logs[-MAX_LOG_LINES:]
        if clean_message:
            state.current_step = clean_message


def _serialize_run(state: AgentRunState) -> dict[str, Any]:
    with RUN_LOCK:
        return {
            "run_id": state.run_id,
            "industry_id": state.industry_id,
            "kind": state.kind,
            "status": state.status,
            "current_step": state.current_step,
            "report_path": state.report_path,
            "command": state.command,
            "logs": list(state.logs),
            "started_at": state.started_at,
            "ended_at": state.ended_at,
            "returncode": state.returncode,
        }


def _register_completed_run(kind: str, industry_id: str, report_path: str | None, logs: list[str] | None = None) -> dict[str, Any]:
    state = AgentRunState(
        run_id=uuid4().hex[:12],
        industry_id=industry_id,
        kind=kind,
        status="completed",
        current_step="已完成",
        report_path=report_path,
        logs=logs or [],
        ended_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        returncode=0,
    )
    with RUN_LOCK:
        RUNS[state.run_id] = state
    return _serialize_run(state)


def _start_subprocess_run(kind: str, industry_id: str, command: list[str], report_path: Path) -> dict[str, Any]:
    state = AgentRunState(
        run_id=uuid4().hex[:12],
        industry_id=industry_id,
        kind=kind,
        report_path=str(report_path),
        command=command,
    )
    with RUN_LOCK:
        RUNS[state.run_id] = state

    def worker() -> None:
        _append_log(state, "启动命令：" + " ".join(command))
        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with RUN_LOCK:
                state.process = process
            assert process.stdout is not None
            for line in process.stdout:
                _append_log(state, line)
            returncode = process.wait()
            with RUN_LOCK:
                state.returncode = returncode
                state.ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if state.status == "canceling":
                    state.status = "canceled"
                    state.current_step = "已中断"
                elif returncode == 0:
                    state.status = "completed"
                    state.current_step = "已完成"
                else:
                    state.status = "failed"
                    state.current_step = f"运行失败，退出码 {returncode}"
        except Exception as exc:
            _append_log(state, f"运行异常：{exc}")
            with RUN_LOCK:
                state.status = "failed"
                state.current_step = "运行异常"
                state.ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    threading.Thread(target=worker, daemon=True).start()
    return _serialize_run(state)


def run_search_plan(industry_id: str, industry_name: str | None) -> dict[str, Any]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_search_plan(industry_id, industry_name or industry_id)
    plan_path = output_dir / "search_plan.json"
    write_json(plan_path, plan)
    return _register_completed_run("search_plan", industry_id, str(plan_path), ["搜索规划已生成。"])


def run_final_validate(industry_id: str) -> dict[str, Any]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "pre_validation_candidate_graph.json"
    if not input_path.exists():
        raise FileNotFoundError("找不到 pre_validation_candidate_graph.json，请先运行构建生成校验前候选图谱。")
    command = [
        sys.executable,
        "tools/agent/final_validate_graph.py",
        "--industry-id",
        industry_id,
    ]
    return _start_subprocess_run("final_validate", industry_id, command, output_dir / "validation_report.md")


def _build_candidate_command(industry_id: str, industry_name: str | None, target_depth: str, stage: str) -> list[str]:
    command = [
        sys.executable,
        "tools/agent/build_candidate_graph.py",
        "--industry-id",
        industry_id,
        "--target-depth",
        target_depth,
        "--strategy",
        "staged",
        "--stage",
        stage,
    ]
    if industry_name:
        command.extend(["--industry-name", industry_name])
    return command


def run_build_skeleton(industry_id: str, industry_name: str | None, target_depth: str) -> dict[str, Any]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = _build_candidate_command(industry_id, industry_name, target_depth, "skeleton")
    return _start_subprocess_run("build_skeleton", industry_id, command, output_dir / "staged_level1_evaluation.json")


def run_build_branches(industry_id: str, industry_name: str | None, target_depth: str) -> dict[str, Any]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not (output_dir / "staged_level1_graph.json").exists():
        raise FileNotFoundError("找不到 staged_level1_graph.json，请先运行一级骨架构建。")
    command = _build_candidate_command(industry_id, industry_name, target_depth, "branches")
    return _start_subprocess_run("build_branches", industry_id, command, output_dir / "pre_validation_candidate_graph.json")


def run_update(industry_id: str, mode: str) -> dict[str, Any]:
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "tools/agent/update_graph.py",
        "--industry-id",
        industry_id,
        "--mode",
        mode,
    ]
    return _start_subprocess_run("update", industry_id, command, output_dir / "update_report.md")


def get_run(run_id: str) -> dict[str, Any]:
    with RUN_LOCK:
        state = RUNS.get(run_id)
    if state is None:
        raise FileNotFoundError(f"Agent run not found: {run_id}")
    return _serialize_run(state)


def cancel_run(run_id: str) -> dict[str, Any]:
    with RUN_LOCK:
        state = RUNS.get(run_id)
        process = state.process if state else None
        if state is None:
            raise FileNotFoundError(f"Agent run not found: {run_id}")
        if state.status not in {"running", "canceling"}:
            return _serialize_run(state)
        state.status = "canceling"
        state.current_step = "正在中断"
    if process and process.poll() is None:
        process.terminate()
        for _ in range(20):
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.poll() is None:
            process.kill()
    _append_log(state, "用户请求中断运行。")
    return _serialize_run(state)


def export_csv(industry_id: str) -> dict[str, str]:
    return export_industry_csv(industry_id)


def _merge_checked_sources(industry_id: str, proposal_path: Path) -> None:
    if not proposal_path.exists():
        return
    proposal = read_json(proposal_path)
    checked_sources = proposal.get("checked_sources", []) or []
    if not checked_sources:
        return
    sources_path = industry_dir(industry_id) / "sources.jsonl"
    existing_sources = read_jsonl(sources_path)
    seen = {(item.get("content_hash"), item.get("url")) for item in existing_sources}
    merged = list(existing_sources)
    for source in checked_sources:
        key = (source.get("content_hash"), source.get("url"))
        if key not in seen:
            merged.append(source)
            seen.add(key)
    write_jsonl(sources_path, merged)


def apply_candidate_graph(industry_id: str, candidate_type: str) -> dict[str, Any]:
    if candidate_type not in {"candidate_graph", "update_candidate_graph"}:
        raise FileNotFoundError(f"Unknown candidate type: {candidate_type}")

    output_dir = industry_dir(industry_id)
    filename = "candidate_graph.json" if candidate_type == "candidate_graph" else "update_candidate_graph.json"
    source_path = output_dir / filename
    if not source_path.exists():
        raise FileNotFoundError(f"Candidate graph not found: {filename}")

    candidate = standardize_graph(read_json(source_path), industry_id)
    validation = validate_graph(candidate, industry_id)
    validation_path = output_dir / "validation_report.md"
    write_markdown_report(validation, validation_path)
    write_json(validation_path.with_suffix(".json"), validation)
    if validation.get("error_count", 1) > 0:
        raise ValueError(f"候选图谱硬规则校验仍有 {validation.get('error_count')} 个错误，请先修正后再应用。")

    write_json(output_dir / "graph.json", candidate)
    if candidate_type == "update_candidate_graph":
        _merge_checked_sources(industry_id, output_dir / "update_proposal.json")
    export = export_graph_csv(candidate, industry_id)
    label = "候选图谱" if candidate_type == "candidate_graph" else "更新候选图谱"
    logs = [
        f"已校验 {filename}，状态：{validation.get('status')}。",
        f"已将{label}写入正式 graph.json。",
        f"已刷新 CSV：{export.get('node_csv')} / {export.get('edge_csv')}。",
    ]
    return _register_completed_run("apply_candidate", industry_id, str(validation_path), logs)


def list_exports(industry_id: str) -> list[str]:
    exports_dir = industry_dir(industry_id) / "exports"
    if not exports_dir.exists():
        return []
    return [str(path) for path in sorted(exports_dir.glob("*.csv"))]


def read_report(path_value: str) -> str:
    path = Path(path_value)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def list_agent_artifacts(industry_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for spec in ARTIFACT_SPECS:
        path = _artifact_path(industry_id, spec)
        exists = path.exists()
        stat = path.stat() if exists else None
        items.append(
            {
                "name": spec.name,
                "label": spec.label,
                "kind": spec.kind,
                "path": str(path),
                "exists": exists,
                "size_bytes": stat.st_size if stat else 0,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds") if stat else None,
            }
        )
    return items


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_agent_artifact(industry_id: str, name: str) -> dict[str, Any]:
    spec = ARTIFACT_BY_NAME.get(name)
    if spec is None:
        raise FileNotFoundError(f"Unknown artifact: {name}")
    path = _artifact_path(industry_id, spec)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {name}")
    if spec.kind == "json":
        content: Any = json.loads(path.read_text(encoding="utf-8"))
    elif spec.kind == "jsonl":
        content = _read_jsonl(path)
    else:
        content = path.read_text(encoding="utf-8")
    return {
        "industry_id": industry_id,
        "name": spec.name,
        "label": spec.label,
        "kind": spec.kind,
        "path": str(path),
        "content": content,
    }

def delete_agent_artifact(industry_id: str, name: str) -> dict[str, Any]:
    spec = ARTIFACT_BY_NAME.get(name)
    if spec is None:
        raise FileNotFoundError(f"Unknown artifact: {name}")
    path = _artifact_path(industry_id, spec)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {name}")
    path.unlink()
    return {
        "industry_id": industry_id,
        "name": spec.name,
        "label": spec.label,
        "path": str(path),
        "deleted": True,
    }








