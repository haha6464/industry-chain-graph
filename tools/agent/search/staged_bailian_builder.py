from __future__ import annotations

import json
import os
from typing import Any

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import now_iso, standardize_graph, write_json
from tools.agent.search.bailian_responses_agent import _extract_json_object, _response_text

DEFAULT_BRANCH_TARGET = "8-12 个新增节点，优先覆盖 level=2/3；只有核心产品、原料、设备等证据充分的分支才展开到 level=4"
INVESTMENT_RESEARCH_NODE_POLICY = """
投研产业链节点口径：
- 目标读者是证券/金融投研人员，节点应是可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元。
- 优先抽取：上游资源/原材料/关键材料/核心零部件、生产制造或服务交付环节、关键工艺/技术路线、专用设备/基础设施、产品或服务形态、下游应用/需求场景、必要的渠道/物流/检测/认证/运维等支撑环节。
- 不要抽取：公司/品牌/股票/财务指标、新闻事件、政策标题、报告标题、市场规模/趋势、消费者画像、平台能力、泛咨询服务、纯管理动作、过度技术方案、营销概念。
- 节点名称必须是行业名词短语，避免“解决方案/平台/体系/网络/SaaS/咨询/研究/管理/服务能力”等泛化能力词单独成节点；确有必要时应上收为更稳定的细分赛道。
- 同一父节点下兄弟节点粒度要一致：不能把“行业大类”和“单一产品/单项技术/单个服务模式”放在同一级；不能一边是大类，一边是单品、技术方案或运营动作。
- 深度要均衡：一级分支通常展开到 L3；核心供给、生产/转换、关键材料、核心零部件、专用设备、重要产品/服务分支可到 L4；渠道、物流、检测认证、运维、咨询等支撑分支通常止于 L2-L3，除非有清晰且稳定的产业子类。
""".strip()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "level": node.get("level"),
        "node_type": node.get("node_type"),
        "chain_position": node.get("chain_position"),
        "parent_id": node.get("parent_id"),
    }


def _json_schema_hint(industry_id: str, industry_name: str) -> str:
    return f"""
请返回严格 JSON，不要 Markdown、解释文字或代码块。结构如下：
{{
  "industry": "{industry_name}",
  "version": "v0.2-staged-build",
  "schema_version": "standard_industry_graph_v0.2_agent",
  "generated_at": "",
  "scope": "面向证券/金融投研的分阶段产业链图谱构建；不包含公司节点、股票代码、财务指标、新闻事件、政策标题、市场规模或营销概念。",
  "source_basis": [{{"name": "资料标题或机构名称", "url": "https://...", "note": "该来源支持的产业链判断"}}],
  "nodes": [{{
    "id": "{industry_id}_001",
    "name": "节点名称",
    "node_type": "产业链/一级环节/二级环节/细分环节/原材料/产品/工艺/设备/渠道/应用场景/支撑服务",
    "tags": ["level_1"],
    "industry": "{industry_name}",
    "level": 1,
    "chain_position": "root/upstream/midstream/downstream/support",
    "parent_id": "",
    "description": "一句话业务描述",
    "business_description": "一句话业务描述",
    "is_key_node": true,
    "chain_segment": "位置标签，不是层级名称",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}],
  "edges": [{{
    "source": "父节点或上游节点 id",
    "target": "子节点或下游节点 id",
    "relation_type": "contains/upstream_downstream",
    "relation_weight": 1.0,
    "description": "关系说明",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}]
}}
""".strip()


def build_seed_prompt(industry_id: str, industry_name: str, target_depth: str) -> str:
    return f"""
你是证券研究场景的产业链图谱构建 Agent。请联网搜索公开资料，为“{industry_name}”先构建面向金融投研的产业链一级骨架。

这张图谱给证券/金融投研人员使用，用于理解上游成本、中游制造、下游渠道/需求、配套支撑之间的产业传导关系，不是企业名录、资讯摘要或泛百科分类。

本次只负责：行业根节点 + level=1 一级产业链环节。

{INVESTMENT_RESEARCH_NODE_POLICY}

硬性要求：
1. 必须联网搜索，不要只依赖模型内部知识。
2. 不要抽取公司节点，不要公司列表，不要股票代码、财务指标或个股信息。
3. level=0 只能有 1 个行业根节点，名称为“{industry_name}”。
4. level=1 覆盖该行业主要一级环节，建议 6-9 个，至少 5 个；优先形成“上游供给-生产/转换或服务交付-产品/服务形态-下游应用/需求-基础设施与必要支撑”的投研分析框架。
5. level=1 不要命名为“上游/中游/下游”；名称必须是稳定产业环节，不要用“咨询研究、数字化平台、解决方案、市场服务”等泛服务能力做一级节点。
6. 只输出 contains 关系：根节点 -> 一级环节。
7. 每个节点和关系必须保留至少 1 个 URL 来源。
8. 总体构建目标为：{target_depth}；本阶段只打宽骨架，不深挖。

{_json_schema_hint(industry_id, industry_name)}
""".strip()


def build_branch_prompt(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    seed_graph: dict[str, Any],
    branch_node: dict[str, Any],
) -> str:
    compact_nodes = [_compact_node(node) for node in seed_graph.get("nodes", [])]
    existing_ids = [node.get("id") for node in seed_graph.get("nodes", [])]
    return f"""
你是证券研究场景的产业链图谱构建 Agent。请联网搜索公开资料，扩展“{industry_name}”产业链中的一个一级分支。

这张图谱给证券/金融投研人员使用，节点必须能服务于行业比较、成本拆解、上下游传导或公司业务归因。

{INVESTMENT_RESEARCH_NODE_POLICY}

当前分支：
{json.dumps(_compact_node(branch_node), ensure_ascii=False)}

已有一级骨架节点：
{json.dumps(compact_nodes, ensure_ascii=False)}

已有节点 ID，新增节点不要重复使用这些 ID：
{json.dumps(existing_ids, ensure_ascii=False)}

本次只负责扩展该分支，不要重写整张图。

硬性要求：
1. 必须联网搜索，不要只依赖模型内部知识。
2. 不要抽取公司节点，不要公司列表，不要股票代码、财务指标或个股信息。
3. 输出该分支下的 level=2/3/4 子节点，目标为 {DEFAULT_BRANCH_TARGET}；不要为了凑深度生成不稳定或过细节点。
4. 重点补横向兄弟分支，不要只沿一条链深挖；同一父节点下兄弟节点的粒度和命名范式必须一致。
5. 每个新增节点都应通过 parent_id 和 contains 关系挂到该分支或其下级节点。
6. 可以补充该分支内部明确的 upstream_downstream 关系，但不要用它替代 contains 层级。
7. 只输出与该分支相关的节点和关系；可以重复输出当前分支节点作为父节点，但不要输出其他一级分支。
8. 每个节点和关系必须保留至少 1 个 URL 来源。
9. 总体构建目标为：{target_depth}；若当前分支属于渠道、物流、检测认证、运维、咨询等支撑环节，通常止于 L2-L3；若属于核心供给、生产/转换、关键材料、核心零部件、专用设备、重要产品/服务，可在证据充分时到 L4。

{_json_schema_hint(industry_id, industry_name)}
""".strip()


def _call_json_prompt(prompt: str, purpose: str) -> tuple[dict[str, Any], str]:
    response = call_bailian_responses(prompt, purpose)
    raw_text = _response_text(response)
    return _extract_json_object(raw_text), raw_text


def call_bailian_seed_graph(industry_id: str, industry_name: str, target_depth: str) -> tuple[dict[str, Any], str, str]:
    prompt = build_seed_prompt(industry_id, industry_name, target_depth)
    graph, raw_text = _call_json_prompt(prompt, "一级骨架构建")
    return graph, raw_text, prompt


def call_bailian_branch_graph(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    seed_graph: dict[str, Any],
    branch_node: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    prompt = build_branch_prompt(industry_id, industry_name, target_depth, seed_graph, branch_node)
    graph, raw_text = _call_json_prompt(prompt, f"分支扩展 {branch_node.get('name', '')}")
    return graph, raw_text, prompt


def _merge_source_basis(graphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for graph in graphs:
        for source in graph.get("source_basis", []) or []:
            url = source.get("url") or source.get("name")
            if url and url not in seen:
                rows.append(source)
                seen.add(url)
    return rows


def merge_staged_graphs(
    industry_id: str,
    industry_name: str,
    seed_graph: dict[str, Any],
    branch_graphs: list[dict[str, Any]],
) -> dict[str, Any]:
    graphs = [seed_graph, *branch_graphs]
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    for graph in graphs:
        standardized = standardize_graph(graph, industry_id)
        for node in standardized.get("nodes", []) or []:
            node_id = node.get("id")
            if not node_id:
                continue
            if node_id not in nodes_by_id:
                nodes_by_id[node_id] = node
            else:
                existing = nodes_by_id[node_id]
                existing["source_urls"] = sorted(set(existing.get("source_urls", []) + node.get("source_urls", [])))
                existing["evidence_ids"] = sorted(set(existing.get("evidence_ids", []) + node.get("evidence_ids", [])))
                existing["confidence"] = max(float(existing.get("confidence", 0)), float(node.get("confidence", 0)))
        for edge in standardized.get("edges", []) or []:
            source = edge.get("source")
            target = edge.get("target")
            relation_type = edge.get("relation_type")
            if not source or not target or not relation_type:
                continue
            key = (source, relation_type, target)
            if key not in edges_by_key:
                edges_by_key[key] = edge
            else:
                existing = edges_by_key[key]
                existing["source_urls"] = sorted(set(existing.get("source_urls", []) + edge.get("source_urls", [])))
                existing["evidence_ids"] = sorted(set(existing.get("evidence_ids", []) + edge.get("evidence_ids", [])))
                existing["confidence"] = max(float(existing.get("confidence", 0)), float(edge.get("confidence", 0)))

    merged = {
        "industry": industry_name,
        "version": "v0.2-staged-build",
        "schema_version": "standard_industry_graph_v0.2_agent",
        "generated_at": now_iso(),
        "scope": "面向证券/金融投研的分阶段产业链图谱构建；目标 60-100 个节点，硬上限 150 个节点；不包含公司节点、股票代码、财务指标、新闻政策、市场趋势或泛服务平台概念。",
        "source_basis": _merge_source_basis(graphs),
        "nodes": list(nodes_by_id.values()),
        "edges": list(edges_by_key.values()),
    }
    return standardize_graph(merged, industry_id)


def staged_branch_limit() -> int:
    return _env_int("BAILIAN_STAGED_MAX_BRANCHES", 8)


def write_staged_artifacts(output_dir, seed_graph: dict[str, Any], fragments: list[dict[str, Any]], merged_graph: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    write_json(output_dir / "staged_level1_graph.json", seed_graph)
    write_json(output_dir / "staged_branch_fragments.json", {"items": fragments})
    write_json(output_dir / "staged_merged_graph.json", merged_graph)
    write_json(output_dir / "staged_errors.json", {"items": errors})



