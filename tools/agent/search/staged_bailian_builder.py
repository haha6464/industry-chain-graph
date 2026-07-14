from __future__ import annotations

import json
import os
from typing import Any

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import now_iso, standardize_graph, write_json
from tools.agent.search.bailian_responses_agent import _extract_json_object, _response_text

DEFAULT_BRANCH_TARGET = "10-16 个新增节点，必须覆盖 level=2/3；核心产品、材料、设备、渠道或应用分支在证据充分时应继续展开到 level=4，少数稳定品类可到 level=5"
INVESTMENT_RESEARCH_NODE_POLICY = """
投研产业链节点口径：
- 目标读者是证券/金融投研人员，节点应是可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元。
- 优先抽取：上游资源/原材料/关键材料/核心零部件、稳定的产品或服务品类、专用设备/基础设施、下游渠道/应用/需求场景、必要的物流/检测/认证/运维等支撑环节。
- 生产制造类节点应优先落到“产品品类/材料品类/设备品类/服务品类/应用场景”，不要拆成单个生产动作或连续工艺步骤。
- 不要抽取：公司/品牌/股票/财务指标、新闻事件、政策标题、报告标题、市场规模/趋势、消费者画像、平台能力、泛咨询服务、纯管理动作、过度技术方案、营销概念、工艺流程步骤。
- 尤其避免把“制备、清洗、破碎、混合、发酵、蒸馏、陈酿、勾调、灌装、包装、检测动作、运输动作”等单个流程动作作为节点；确有投研意义时，应上收为更稳定的产品/材料/设备/品类节点。
- 节点名称必须是行业名词短语，避免“解决方案/平台/体系/网络/SaaS/咨询/研究/管理/服务能力”等泛化能力词单独成节点；确有必要时应上收为更稳定的细分赛道。
- 同一父节点下兄弟节点粒度要一致：不能把“行业大类”和“单一产品/单项技术/单个服务模式”放在同一级；不能一边是大类，一边是单品、技术方案或运营动作。
- 深度要均衡但不能过浅：一级分支通常至少展开到 L3；核心供给、关键材料、核心零部件、专用设备、重要产品/服务分支在证据充分时应展开到 L4，少数稳定品类可到 L5；渠道、物流、检测认证、运维、咨询等支撑分支通常止于 L3-L4，除非有清晰且稳定的产业子类。
- 未来会接入公司节点，因此节点应便于公司主营业务挂载；避免一个公司会同时挂到多个连续工艺步骤的节点设计。
- 关系语义固定：contains 表示分类隶属；upstream_downstream 只用于 L0 与 L1 之间判断某个一级环节属于行业根节点的上游或下游，L2 以下不要输出 upstream_downstream。
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
  "scope": "面向证券/金融投研的分阶段产业链图谱构建；L2 以下只表达分类隶属；不包含公司节点、股票代码、财务指标、新闻事件、政策标题、市场规模、营销概念或工艺流程节点。",
  "source_basis": [{{"name": "资料标题或机构名称", "url": "https://...", "note": "该来源支持的产业链判断"}}],
  "nodes": [{{
    "id": "{industry_id}_001",
    "name": "节点名称",
    "node_type": "产业链/一级环节/二级环节/细分环节/原材料/材料/产品/设备/渠道/应用场景/支撑服务",
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
6. level=1 的关系按语义输出：
   - 若一级环节 chain_position 是 upstream，parent_id 留空，输出一条 upstream_downstream：一级环节 -> 行业根节点，表示它是行业上游。
   - 若一级环节 chain_position 是 downstream，parent_id 留空，输出一条 upstream_downstream：行业根节点 -> 一级环节，表示它是行业下游。
   - 若一级环节不属于上游/下游（如 midstream/support），parent_id 填行业根节点 id，输出 contains：行业根节点 -> 一级环节。
   - 同一个 L0-L1 节点对不要同时输出 contains 和 upstream_downstream。
7. 每个节点和关系必须保留至少 1 个 URL 来源。
8. 总体构建目标为：{target_depth}；本阶段只打宽骨架，不深挖，但需要为后续哪些一级分支可展开到 L4/L5 留出清晰主干。

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
3. 输出该分支下的 level=2/3/4/5 子节点，目标为 {DEFAULT_BRANCH_TARGET}；不要为了凑深度生成不稳定或过细节点，但核心产业链分支不能全部停在 L3。
4. 先补横向兄弟分支，再选择 1-3 条最核心、证据最充分的子链继续下钻到 L4/L5；同一父节点下兄弟节点的粒度和命名范式必须一致。
5. 每个新增节点都必须且只能有一个 parent_id，并且只输出一条与 parent_id 完全一致的 contains 父子关系；不要让同一个节点挂到多个父节点。
6. 本阶段只输出 contains 父子隶属关系；不要在 L2 以下输出 upstream_downstream，也不要用工序流向关系替代分类层级。
7. 只输出与该分支相关的节点和关系；可以重复输出当前分支节点作为父节点，但不要输出其他一级分支。若该分支适合深挖，应保证至少一条 contains 路径达到 L4 或 L5。
8. 每个节点和关系必须保留至少 1 个 URL 来源。
9. 总体构建目标为：{target_depth}；若当前分支属于核心供给、关键材料、核心零部件、专用设备、重要产品/服务，应优先形成 L4 深度，少数稳定品类可到 L5；若属于渠道、物流、检测认证、运维、咨询等支撑环节，通常止于 L3-L4，确有稳定产业子类时可到 L5。
10. 禁止把单个工艺流程步骤作为节点。请优先使用品类、材料、设备、渠道、应用场景、消费/需求类别等能承接公司主营业务的节点。例如不要输出“制曲/发酵/蒸馏/陈酿/勾调/灌装包装”这类连续工序节点，应上收或改写为“产品香型/价格带/细分品类/设备品类/渠道场景”等稳定投研分类。

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
        "scope": "面向证券/金融投研的分阶段产业链图谱构建；目标 100 个以上节点；L2 以下只表达分类隶属；不包含公司节点、股票代码、财务指标、新闻政策、市场趋势、泛服务平台概念或工艺流程节点。",
        "source_basis": _merge_source_basis(graphs),
        "nodes": list(nodes_by_id.values()),
        "edges": list(edges_by_key.values()),
    }
    return standardize_graph(merged, industry_id)


def staged_branch_limit(total_branches: int) -> int:
    configured = _env_int("BAILIAN_STAGED_BRANCH_LIMIT", 0)
    if configured <= 0 or configured >= total_branches:
        return total_branches
    return configured


def write_staged_artifacts(output_dir, seed_graph: dict[str, Any], fragments: list[dict[str, Any]], merged_graph: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    write_json(output_dir / "staged_level1_graph.json", seed_graph)
    write_json(output_dir / "staged_branch_fragments.json", {"items": fragments})
    write_json(output_dir / "staged_merged_graph.json", merged_graph)
    write_json(output_dir / "staged_errors.json", {"items": errors})








