from __future__ import annotations

import copy
import json
import os
from typing import Any

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import now_iso, standardize_graph, write_json
from tools.agent.search.bailian_responses_agent import _extract_json_object, _response_text

DEFAULT_BRANCH_TARGET = "10-16 个新增节点，必须覆盖 level=2/3；核心产品、材料、设备、渠道或应用分支在证据充分时应继续展开到 level=4"
MIN_BRANCH_NEW_NODES = 8
SKELETON_CLASSIFICATION_POLICY = """
一级骨架分类原则：
- 先判断行业原型，再选择一级分类轴：资源品行业通常按资源/材料/加工/产品/需求拆解；制造业通常按关键供给/核心产品/渠道应用拆解；消费品通常按原料/产品品类/渠道终端拆解；服务与网络型行业通常优先按可投资的业务赛道、服务方式或基础设施类型拆解；综合行业可以使用混合框架，但必须说明主轴，且尽量减少重叠。
- L1 必须围绕一个明确的主分类轴组织。不要把“原材料、设备、能源”等供给要素，与另一套产品或业务赛道分类无原则地并列；确需并列时，要证明它们都是独立、重要且可继续展开的产业链分支。
- 优先参考权威行业分类、券商覆盖口径和企业主营业务分部，先界定行业核心经营活动，再补充真正重要的上游供给和支撑环节。不要把相邻行业的通用材料、通用金融、通用咨询或通用 IT 服务扩成一级分支。
- L1 应尽量互斥、合计完整、粒度相当。若一个一级节点的描述一次性枚举了多个重要可投资赛道，而其他一级节点只是单一要素或窄支撑项，说明骨架失衡，应重新拆分。
- 每个 L1 都应有清晰的后续展开路径，通常能形成 8-20 个有投研意义的下级节点；避免“一个超大综合分支 + 多个很窄支撑分支”的结构。
- 一级骨架的目标不是套用固定的“原料-制造-渠道”模板，而是找出该行业最能解释收入池、成本项、供需驱动和公司业务归属的分类方式。
""".strip()
INVESTMENT_RESEARCH_NODE_POLICY = """
投研产业链节点口径：
- 目标读者是证券/金融投研人员，节点应是可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元。
- 优先抽取：上游资源/原材料/关键材料/核心零部件、稳定的产品或服务品类、专用设备/基础设施、下游渠道/应用/需求场景、必要的物流/检测/认证/运维等支撑环节。
- 生产制造类节点应优先落到“产品品类/材料品类/设备品类/服务品类/应用场景”，不要拆成单个生产动作或连续工艺步骤。
- 不要抽取：公司/品牌/股票/财务指标、新闻事件、政策标题、报告标题、市场规模/趋势、消费者画像、平台能力、泛咨询服务、纯管理动作、过度技术方案、营销概念、工艺流程步骤。
- 尤其避免把“制备、清洗、破碎、混合、发酵、蒸馏、陈酿、勾调、灌装、包装、检测动作、运输动作”等单个流程动作作为节点；确有投研意义时，应上收为更稳定的产品/材料/设备/品类节点。
- 节点名称必须是行业名词短语，避免“解决方案/平台/体系/网络/SaaS/咨询/研究/管理/服务能力”等泛化能力词单独成节点；确有必要时应上收为更稳定的细分赛道。
- 同一父节点下兄弟节点粒度要一致：不能把“行业大类”和“单一产品/单项技术/单个服务模式”放在同一级；不能一边是大类，一边是单品、技术方案或运营动作。
- 深度要均衡但不能过浅：一级分支通常至少展开到 L3；核心供给、关键材料、核心零部件、专用设备、重要产品/服务分支在证据充分时应展开到 L4；渠道、物流、检测认证、运维、咨询等支撑分支通常止于 L3-L4，除非有清晰且稳定的产业子类。
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


def build_seed_blueprint_prompt(industry_name: str) -> str:
    return f"""
你是券商行业研究员。请联网研究“{industry_name}”，先设计产业链一级骨架的分类蓝图；此时不要生成图谱节点、关系、ID 或其他工程字段，只关注行业边界与分类质量。

{SKELETON_CLASSIFICATION_POLICY}

研究要求：
1. 至少交叉参考权威行业分类或监管统计口径、券商/评级机构行业研究、产业链资料三类来源；不要只依赖一张泛化产业链图。
2. 明确该行业属于资源品、制造、消费品、服务网络或综合型中的哪种原型，并解释最适合它的一级主分类轴。
3. 区分核心经营赛道、重要上游供给、下游需求和必要支撑；把相邻行业或通用外包能力列入排除项。
4. 设计 6-10 个 L1 候选。逐项说明纳入理由、边界、预计下钻方向，并检查重叠、遗漏和粒度失衡。
5. 特别检查是否存在某个候选分支吞并了大多数核心业务赛道；如有，拆开或改用更合适的主分类轴。

请返回严格 JSON，不要 Markdown：
{{
  "industry_archetype": "resource/manufacturing/consumer/service_network/mixed",
  "industry_boundary": "行业核心经营活动与研究边界",
  "primary_classification_axis": "一级骨架采用的主分类轴及理由",
  "level_one_design": [{{
    "name": "建议的一级环节名",
    "role": "core/upstream/downstream/support",
    "chain_position": "upstream/midstream/downstream/support",
    "inclusion": "纳入哪些稳定业务或品类",
    "exclusion": "不纳入哪些相邻概念",
    "expansion_outline": ["后续二级分类方向"]
  }}],
  "excluded_adjacent_areas": ["不应进入一级骨架的相邻行业或通用能力"],
  "balance_check": "互斥性、完整性、粒度和分支容量检查结论",
  "source_basis": [{{"name": "来源", "url": "https://...", "note": "支持的分类判断"}}]
}}
""".strip()


def build_seed_prompt(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    blueprint: dict[str, Any] | None = None,
) -> str:
    return f"""
你是证券研究场景的产业链图谱构建 Agent。请联网搜索公开资料，为“{industry_name}”先构建面向金融投研的产业链一级骨架。

这张图谱给证券/金融投研人员使用，用于理解上游成本、中游制造、下游渠道/需求、配套支撑之间的产业传导关系，不是企业名录、资讯摘要或泛百科分类。

本次只负责：行业根节点 + level=1 一级产业链环节。

已完成的行业边界与一级分类蓝图如下。除非联网证据表明蓝图存在明显事实错误，否则一级节点应服从其中的行业边界、主分类轴和排除项，不要重新套用通用产业链模板：
{json.dumps(blueprint or {}, ensure_ascii=False)}

{SKELETON_CLASSIFICATION_POLICY}

{INVESTMENT_RESEARCH_NODE_POLICY}

硬性要求：
1. 必须联网搜索，不要只依赖模型内部知识。
2. 不要抽取公司节点，不要公司列表，不要股票代码、财务指标或个股信息。
3. level=0 只能有 1 个行业根节点，名称为“{industry_name}”。
4. level=1 覆盖该行业主要一级环节，建议 6-10 个，至少 5 个；必须沿用分类蓝图确定的主轴，并兼顾核心经营赛道、重要供给、下游需求与必要支撑。
5. level=1 不要命名为“上游/中游/下游”；名称必须是稳定产业环节，不要用“咨询研究、数字化平台、解决方案、市场服务”等泛服务能力做一级节点。
   同时检查 L1 是否互斥、粒度相当：不要让一个“综合运营/综合制造”节点吞并多个主要赛道，而其余节点只是窄小的材料、能源、运维或 IT 支撑。
6. level=1 的关系按语义输出：
   - 若一级环节 chain_position 是 upstream，parent_id 留空，输出一条 upstream_downstream：一级环节 -> 行业根节点，表示它是行业上游。
   - 若一级环节 chain_position 是 downstream，parent_id 留空，输出一条 upstream_downstream：行业根节点 -> 一级环节，表示它是行业下游。
   - 若一级环节不属于上游/下游（如 midstream/support），parent_id 填行业根节点 id，输出 contains：行业根节点 -> 一级环节。
   - 同一个 L0-L1 节点对不要同时输出 contains 和 upstream_downstream。
7. 每个节点和关系必须保留至少 1 个 URL 来源。
8. 总体构建目标为：{target_depth}；本阶段只打宽骨架，不深挖，但需要为后续哪些一级分支可展开到 L4 留出清晰主干。

{_json_schema_hint(industry_id, industry_name)}
""".strip()


def build_branch_prompt(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    seed_graph: dict[str, Any],
    branch_node: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> str:
    compact_nodes = [_compact_node(node) for node in seed_graph.get("nodes", [])]
    existing_ids = [node.get("id") for node in seed_graph.get("nodes", [])]
    branch_id = str(branch_node.get("id", ""))
    return f"""
你是证券研究场景的产业链图谱构建 Agent。请联网搜索公开资料，扩展“{industry_name}”产业链中的一个一级分支。

这张图谱给证券/金融投研人员使用，节点必须能服务于行业比较、成本拆解、上下游传导或公司业务归因。

{INVESTMENT_RESEARCH_NODE_POLICY}

行业边界与一级分类蓝图：
{json.dumps(blueprint or {}, ensure_ascii=False)}

分支扩展必须遵守蓝图中的行业边界、当前 L1 的 inclusion/exclusion 和主分类轴。不要把蓝图排除的相邻行业或通用能力重新引入下级节点。

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
3. 输出该分支下的 level=2/3/4 子节点，目标为 {DEFAULT_BRANCH_TARGET}；不要为了凑深度生成不稳定或过细节点，但核心产业链分支不能全部停在 L3。
4. 先补横向兄弟分支，再选择 1-3 条最核心、证据最充分的子链继续下钻到 L4；同一父节点下兄弟节点的粒度和命名范式必须一致。
5. 每个新增节点都必须且只能有一个 parent_id，并且只输出一条与 parent_id 完全一致的 contains 父子关系；不要让同一个节点挂到多个父节点。
   新增节点 ID 必须使用当前分支专属前缀“{branch_id}_”，并从“{branch_id}_001”开始编号；禁止使用“{industry_id}_009”这类可能与其他分支重复的全局流水号。
6. 本阶段只输出 contains 父子隶属关系；不要在 L2 以下输出 upstream_downstream，也不要用工序流向关系替代分类层级。
7. 只输出与该分支相关的节点和关系；可以重复输出当前分支节点作为父节点，但不要输出其他一级分支。若该分支适合深挖，应保证至少一条 contains 路径达到 L4。
8. 每个节点和关系必须保留至少 1 个 URL 来源。
9. 总体构建目标为：{target_depth}；若当前分支属于核心供给、关键材料、核心零部件、专用设备、重要产品/服务，应优先形成 L4 深度；若属于渠道、物流、检测认证、运维、咨询等支撑环节，通常止于 L3-L4。
10. 禁止把单个工艺流程步骤作为节点。请优先使用产品或服务品类、材料、设备、渠道、应用场景、需求类别等能承接公司主营业务的节点；原料处理、装配、检测、包装、运输等连续动作应上收为稳定的业务或设备品类。

{_json_schema_hint(industry_id, industry_name)}
""".strip()


def _call_json_prompt(prompt: str, purpose: str) -> tuple[dict[str, Any], str]:
    response = call_bailian_responses(prompt, purpose)
    raw_text = _response_text(response)
    return _extract_json_object(raw_text), raw_text


def call_bailian_seed_blueprint(industry_name: str) -> tuple[dict[str, Any], str, str]:
    prompt = build_seed_blueprint_prompt(industry_name)
    blueprint, raw_text = _call_json_prompt(prompt, "一级骨架分类蓝图")
    level_one_design = blueprint.get("level_one_design")
    if not blueprint.get("primary_classification_axis") or not isinstance(level_one_design, list) or len(level_one_design) < 5:
        raise BailianAgentError("一级骨架分类蓝图缺少主分类轴或有效 L1 设计")
    return blueprint, raw_text, prompt


def call_bailian_seed_graph(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    prompt = build_seed_prompt(industry_id, industry_name, target_depth, blueprint)
    graph, raw_text = _call_json_prompt(prompt, "一级骨架构建")
    return graph, raw_text, prompt


def call_bailian_branch_graph(
    industry_id: str,
    industry_name: str,
    target_depth: str,
    seed_graph: dict[str, Any],
    branch_node: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    prompt = build_branch_prompt(industry_id, industry_name, target_depth, seed_graph, branch_node, blueprint)
    graph, raw_text = _call_json_prompt(prompt, f"分支扩展 {branch_node.get('name', '')}")
    return graph, raw_text, prompt


def namespace_branch_graph(branch_graph: dict[str, Any], branch_node: dict[str, Any]) -> dict[str, Any]:
    graph = copy.deepcopy(branch_graph)
    branch_id = str(branch_node.get("id", "")).strip()
    if not branch_id:
        raise BailianAgentError("一级分支缺少 ID，无法分配分支节点命名空间")

    child_nodes = [node for node in graph.get("nodes", []) or [] if int(node.get("level", 0)) >= 2]
    old_ids = [str(node.get("id", "")).strip() for node in child_nodes]
    if any(not node_id for node_id in old_ids) or len(old_ids) != len(set(old_ids)):
        raise BailianAgentError(f"分支 {branch_node.get('name', branch_id)} 内存在空 ID 或重复节点 ID")

    id_mapping = {
        old_id: f"{branch_id}_{index:03d}"
        for index, old_id in enumerate(old_ids, start=1)
    }
    for node in graph.get("nodes", []) or []:
        old_id = str(node.get("id", "")).strip()
        if old_id in id_mapping:
            node["id"] = id_mapping[old_id]
        parent_id = str(node.get("parent_id", "")).strip()
        if parent_id in id_mapping:
            node["parent_id"] = id_mapping[parent_id]
    for edge in graph.get("edges", []) or []:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source in id_mapping:
            edge["source"] = id_mapping[source]
        if target in id_mapping:
            edge["target"] = id_mapping[target]
        edge.pop("id", None)
    return graph


def validate_branch_expansion(
    branch_graph: dict[str, Any],
    branch_node: dict[str, Any],
    min_new_nodes: int = MIN_BRANCH_NEW_NODES,
) -> None:
    branch_id = str(branch_node.get("id", "")).strip()
    nodes = branch_graph.get("nodes", []) or []
    child_nodes = [node for node in nodes if int(node.get("level", 0)) >= 2]
    if len(child_nodes) < min_new_nodes:
        raise BailianAgentError(
            f"分支 {branch_node.get('name', branch_id)} 仅扩展 {len(child_nodes)} 个节点，低于最低要求 {min_new_nodes}"
        )

    node_by_id = {str(node.get("id", "")): node for node in nodes}
    for node in child_nodes:
        node_id = str(node.get("id", ""))
        parent_id = str(node.get("parent_id", ""))
        visited = {node_id}
        while parent_id and parent_id != branch_id and parent_id not in visited:
            visited.add(parent_id)
            parent = node_by_id.get(parent_id)
            if parent is None:
                break
            parent_id = str(parent.get("parent_id", ""))
        if parent_id != branch_id:
            raise BailianAgentError(
                f"分支 {branch_node.get('name', branch_id)} 的节点 {node_id} 未沿 parent_id 归属于当前 L1"
            )


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
                identity_fields = ("name", "level", "parent_id")
                if any(existing.get(field) != node.get(field) for field in identity_fields):
                    raise BailianAgentError(
                        f"跨分支节点 ID 冲突：{node_id} 同时表示“{existing.get('name')}”和“{node.get('name')}”"
                    )
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
        "scope": "面向证券/金融投研的分阶段产业链图谱构建；节点通常在 120 个以上，不设硬上限；L2 以下只表达分类隶属；不包含公司节点、股票代码、财务指标、新闻政策、市场趋势、泛服务平台概念或工艺流程节点。",
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








