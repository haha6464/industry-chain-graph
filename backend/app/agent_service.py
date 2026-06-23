from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.agent.build_graph import build_graph
from tools.agent.common import industry_dir, write_json
from tools.agent.export_csv import export_industry_csv
from tools.agent.search.search_planner import build_search_plan
from tools.agent.update_graph import update_graph
from tools.agent.validators.graph_validator import validate_industry


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
    ArtifactSpec("semantic_validation_report", "百炼语义校验报告", "semantic_validation_report.json", "json"),
    ArtifactSpec("build_report", "构建报告", "build_report.md", "markdown"),
    ArtifactSpec("update_proposal", "更新提案", "update_proposal.json", "json"),
    ArtifactSpec("update_candidate_graph", "更新候选图谱", "update_candidate_graph.json", "json"),
    ArtifactSpec("update_report", "更新报告", "update_report.md", "markdown"),
    ArtifactSpec("agent_raw_response", "构建 Agent 原始响应", "agent_raw_response.txt", "text"),
    ArtifactSpec("validation_agent_raw_response", "校验 Agent 原始响应", "validation_agent_raw_response.txt", "text"),
    ArtifactSpec("update_agent_raw_response", "更新 Agent 原始响应", "update_agent_raw_response.txt", "text"),
]
ARTIFACT_BY_NAME = {spec.name: spec for spec in ARTIFACT_SPECS}


def _artifact_path(industry_id: str, spec: ArtifactSpec) -> Path:
    base = industry_dir(industry_id).resolve()
    path = (base / spec.relative_path).resolve()
    if base not in path.parents and path != base:
        raise FileNotFoundError(f"Invalid artifact path: {spec.name}")
    return path


def run_search_plan(industry_id: str, industry_name: str | None) -> dict[str, str]:
    run_id = uuid4().hex[:12]
    output_dir = industry_dir(industry_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_search_plan(industry_id, industry_name or industry_id)
    plan_path = output_dir / "search_plan.json"
    write_json(plan_path, plan)
    return {"run_id": run_id, "industry_id": industry_id, "status": "completed", "report_path": str(plan_path)}


def run_validate(industry_id: str) -> dict[str, str]:
    run_id = uuid4().hex[:12]
    report_path = industry_dir(industry_id) / "validation_report.md"
    validate_industry(industry_id, report_path=report_path)
    return {"run_id": run_id, "industry_id": industry_id, "status": "completed", "report_path": str(report_path)}


def run_build(industry_id: str, industry_name: str | None, target_depth: str) -> dict[str, str]:
    run_id = uuid4().hex[:12]
    result = build_graph(industry_id, industry_name, apply=False, target_depth=target_depth)
    return {"run_id": run_id, "industry_id": industry_id, "status": "completed", "report_path": result["build_report"]}


def run_update(industry_id: str, mode: str) -> dict[str, str]:
    run_id = uuid4().hex[:12]
    result = update_graph(industry_id, mode)
    return {"run_id": run_id, "industry_id": industry_id, "status": str(result["status"]), "report_path": str(result["proposal"])}


def export_csv(industry_id: str) -> dict[str, str]:
    return export_industry_csv(industry_id)


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
