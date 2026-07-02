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
    "{industry_name} 券商研究 产业链 上下游 分类",
    "{industry_name} 行业深度报告 产业链 成本构成 细分环节",
    "{industry_name} 投研 产业链图谱 一级环节 二级细分",
    "{industry_name} 上游原材料 中游制造 下游渠道 行业研究",
    "{industry_name} 业务结构 细分环节 上游供给 设备 下游应用",
    "{industry_name} 供应链 价值链 成本拆解 细分赛道",
    "{industry_name} 行业分类 产品分类 终端需求 应用领域",
    "{industry_name} 产业链 全景图 细分环节 研究报告",
]

LEVEL_EXTRACTION_POLICY = {
    "level_definition": "level 是数字层级深度：L0 为行业根节点，L1 为一级投研产业链环节，L2/L3/L4... 为逐级细分的上游供给、关键材料/零部件、工艺/技术路线、设备/基础设施、产品或服务形态、下游应用/需求等稳定产业分类节点。",
    "target_depth": "多数一级分支展开到 L3；核心供给、生产/转换、关键材料、核心零部件、专用设备、重要产品或服务等证据充分时可到 L4；渠道、物流、检测认证、运维、咨询等支撑环节通常止于 L2-L3，不硬凑深度。",
    "not_level_names": "不要把上游/中游/下游作为 level 名称；它们只用于描述节点间流向关系或 chain_position 位置标签。",
    "target_node_count": "目标节点数量 60-100 个，硬上限 150 个。",
    "breadth_requirement": "不要只沿少数分支深挖；level=1 应覆盖主要投研分析环节，重要一级环节应展开 3-8 个二级/三级兄弟分支，优先补齐横向缺失的上游供给、关键材料/零部件、工艺/技术路线、设备/基础设施、产品或服务形态、下游应用/需求和必要支撑服务；避免公司、品牌、新闻政策、市场规模、平台能力、泛咨询、SaaS/解决方案等非产业链节点。",
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


