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
from pathlib import Path

from tools.agent.common import industry_dir, write_json

QUERY_TEMPLATES = [
    "{industry_name} 产业链 上游 中游 下游",
    "{industry_name} 产业链图谱",
    "{industry_name} 行业研究报告 产业链",
    "{industry_name} 原材料 渠道 下游应用",
    "{industry_name} 行业分类 产品结构",
]


def build_search_plan(industry_id: str, industry_name: str) -> dict[str, object]:
    return {
        "industry_id": industry_id,
        "industry_name": industry_name,
        "queries": [template.format(industry_name=industry_name) for template in QUERY_TEMPLATES],
        "provider": "bailian_responses",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate search queries for one industry.")
    parser.add_argument("--industry-id", required=True)
    parser.add_argument("--industry-name", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_search_plan(args.industry_id, args.industry_name)
    output = args.output or industry_dir(args.industry_id) / "search_plan.json"
    write_json(output, plan)
    print(output)
