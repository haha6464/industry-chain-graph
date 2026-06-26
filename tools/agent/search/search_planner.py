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
    "{industry_name} 产业链图谱 层级 细分环节",
    "{industry_name} 行业研究报告 产品结构 产业链",
    "{industry_name} 一级环节 二级环节 细分产品",
    "{industry_name} 原材料 工艺 设备 渠道 应用场景",
    "{industry_name} 行业分类 产品分类 应用领域",
    "{industry_name} 主要产品 细分品类 原材料 工艺 设备 应用",
    "{industry_name} 产业链 一级环节 二级分支 全景",
    "{industry_name} 供应链 产业链 价值链 关系",
]

LEVEL_EXTRACTION_POLICY = {
    "level_definition": "level 是数字层级深度：L0 为行业根节点，L1 为一级产业链环节，L2/L3/L4/L5... 为逐级细分的产品、原料、工艺、渠道、应用等节点。",
    "target_depth": "证据充分时尽量形成 5-6 层左右；证据不足时保持合理粒度，不硬凑层级。",
    "not_level_names": "不要把上游/中游/下游作为 level 名称；它们只用于描述节点间流向关系或 chain_position 位置标签。",
    "target_node_count": "目标节点数量 60-100 个，硬上限 150 个。",
    "breadth_requirement": "不要只沿少数分支深挖；level=1 应覆盖主要一级环节，重要一级环节应展开 3-8 个二级/三级兄弟分支，优先补齐横向缺失的产品类别、原材料、工艺、设备、渠道、应用场景和支撑服务。",
}


def build_search_plan(industry_id: str, industry_name: str) -> dict[str, object]:
    return {
        "industry_id": industry_id,
        "industry_name": industry_name,
        "queries": [template.format(industry_name=industry_name) for template in QUERY_TEMPLATES],
        "level_extraction_policy": LEVEL_EXTRACTION_POLICY,
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
