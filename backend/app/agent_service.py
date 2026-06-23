from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.agent.build_graph import build_graph
from tools.agent.common import industry_dir
from tools.agent.export_csv import export_industry_csv
from tools.agent.update_graph import update_graph


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
