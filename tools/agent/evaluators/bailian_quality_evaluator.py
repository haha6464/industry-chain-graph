from __future__ import annotations

import json
from typing import Any

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import standardize_graph
from tools.agent.search.bailian_responses_agent import _extract_json_object, _response_text

INVESTMENT_RESEARCH_EVALUATION_POLICY = """
投研口径判定标准：
- 合格节点应是证券/金融投研中可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元。
- 优先认可：上游资源/原材料/关键材料/核心零部件、稳定产品或服务品类、专用设备/基础设施、下游应用/需求场景、必要渠道/物流/检测/认证/运维等支撑环节。
- 应判为问题：公司/品牌/股票/财务指标、新闻事件、政策/报告标题、市场规模/趋势、消费者画像、泛咨询服务、平台能力、SaaS/解决方案/体系/网络等过度技术或管理概念单独成节点。
- 应判为问题：把单个工艺流程步骤或生产动作作为节点，例如“制备、清洗、破碎、混合、发酵、蒸馏、陈酿、勾调、灌装、包装、检测动作、运输动作”等；这些内容应上收为稳定的产品品类、材料品类、设备品类、渠道或应用场景。
- 关系语义必须清晰：contains 表示分类隶属；upstream_downstream 只允许存在于 L0 与 L1 之间，用于判断某个一级环节相对行业根节点属于上游或下游；L2 以下出现 upstream_downstream 应判为问题。
- 同一父节点下兄弟节点粒度必须一致；节点数量和深度按当前阶段的专属标准判断，不要把完整图谱指标错误用于一级骨架或单个分支。
- 未来会接入公司节点，节点应便于公司按主营业务挂载；若一个企业会被迫连接到多个连续工艺节点，说明节点设计不合格。
""".strip()

SKELETON_EVALUATION_POLICY = """
一级骨架专属判定：
- 先明确行业边界、行业原型和一级主分类轴，再逐个判断 L1 是否服从同一主轴。对于服务网络型或综合型行业，不得机械套用制造业的“原料-制造-渠道”模板。
- 核心经营赛道、上游供给、下游需求、支撑能力必须有清楚边界。通用材料、通用金融保险、通用咨询、通用 IT 只有在对该行业收入池或成本传导足够重要时才可成为 L1，不能为追求完整而随意纳入相邻行业。
- L1 应尽量互斥、合计完整、粒度相当。如果一个节点描述中枚举了多个重要业务赛道，而其他节点只是单一要素或窄支撑项，应判为主轴混杂或分支容量失衡。
- 每个 L1 必须是可独立扩展的主干。若预计大多数节点都会集中在一个“综合运营/综合制造/综合服务”分支，其他分支明显偏窄，应返回 needs_revision。
- 行业归属边界不等于产业链上下游边界。一级骨架必须至少有 1 个 upstream L1 和 1 个 downstream L1；重要渠道、终端应用或需求场景即使归属于其他一级行业，也不能仅因跨行业归属而排除。任一位置缺失都应返回 needs_revision。
- 一级骨架阶段只评估 L0/L1 的边界、分类轴、覆盖、粒度和可扩展性，不以全图 120 个节点或是否达到 L4 作为本阶段通过条件。
""".strip()

BRANCH_EVALUATION_POLICY = """
单分支专属判定：
- 只评估当前分支内部的覆盖、粒度、命名、树状结构和适当深度，不因其他一级分支尚未扩展而扣分。
- 当前分支通常应有 10-16 个新增节点，覆盖 L2/L3；核心产品、材料、设备或服务赛道在证据充分时至少有一条路径达到 L4。
- 同一父节点下兄弟节点应采用一致分类维度，不能混放大类、单品、技术、运营动作和需求场景。
""".strip()


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "level": node.get("level"),
        "chain_position": node.get("chain_position"),
        "parent_id": node.get("parent_id"),
        "description": node.get("business_description") or node.get("description", ""),
    }


def _compact_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "industry": graph.get("industry"),
        "nodes": [_compact_node(node) for node in graph.get("nodes", [])],
        "edges": [
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
                "relation_type": edge.get("relation_type"),
                "description": edge.get("description", ""),
            }
            for edge in graph.get("edges", [])
        ],
    }


def _quality_eval_prompt(stage: str, industry_name: str, payload: dict[str, Any]) -> str:
    stage_policy = SKELETON_EVALUATION_POLICY if stage == "一级骨架评估" else BRANCH_EVALUATION_POLICY
    return f"""
你是证券研究场景的产业链分类质量评估员。请只判断“{industry_name}”产业链图谱在当前阶段的分类质量，不要关注 JSON 字段写法、ID 格式、URL、置信度等工程格式问题。

这张图谱给证券/金融投研人员使用，质量标准应接近券商行业研究中的产业链拆解，而不是泛百科、资讯摘要或企业名录。

{INVESTMENT_RESEARCH_EVALUATION_POLICY}

{stage_policy}

评估重点：
1. 产业链环节分类是否符合投研行业研究常识，能否支撑成本、价格、供需、上下游传导分析。
2. 当前阶段内部分类是否完整，是否遗漏对该行业真正重要的核心经营赛道、关键供给、产品或服务形态、设备/基础设施、下游需求；不要机械要求所有行业具备完全相同的环节。
3. 节点名称是否是稳定产业名词短语；是否混入平台能力、泛服务、咨询研究、SaaS、解决方案、市场趋势等不适合作为产业链节点的概念。
4. 粒度是否统一，是否把大类、单品、技术方案、运营动作放在同一级；是否存在一个节点挂到多个父节点的非树状结构。
5. 横向覆盖是否充分，是否只沿单一路径深挖；分支深度是否与节点性质匹配，是否至少有核心主链达到 L4。
6. 上游、中游、下游、支持环节的产业逻辑是否自然。
7. 是否存在 L2 以下 upstream_downstream、跨层级工艺流转边，导致上下游关系与隶属关系含义重合。
8. 是否存在不利于后续公司节点挂载的工艺流程节点；如果有，应建议替换为品类、材料、设备、渠道或应用场景节点。

判定规则：只要出现明显不适合作为投研产业链节点的名称、分类主轴混杂、一级分支容量严重失衡、工艺流程步骤节点、L2 以下 upstream_downstream、同级粒度严重不一致、同一节点多父节点、当前分支明显过浅或支撑分支无依据地过深，应返回 needs_revision。

当前阶段：{stage}

请返回严格 JSON：
{{
  "status": "pass/needs_revision",
  "score": 0-100,
  "summary": "一句话结论",
  "primary_axis_assessment": "骨架阶段说明主分类轴是否清晰；分支阶段说明该分支采用的分类维度",
  "blocking_issues": ["导致不通过的实质问题；通过时必须为空"],
  "opinions": ["保留的质量意见"],
  "revision_focus": ["如果需要修改，列出必须调整的产业链分类问题；通过时必须为空"]
}}

输入：
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def _revision_prompt(
    stage: str,
    industry_id: str,
    industry_name: str,
    graph: dict[str, Any],
    evaluation: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> str:
    return f"""
你是产业链图谱分类修正 Agent。请根据评估意见，只修改当前{stage}中的产业链分类质量问题。

注意：
1. 聚焦投研产业链分类质量：补关键环节、合并不合理分类、调整粒度，删除或上收公司/股票/财务概念、平台能力、泛咨询服务、SaaS/解决方案/体系/网络、新闻政策标题、市场趋势等非产业链节点。
2. 删除或改写工艺流程步骤节点，优先替换为适合公司主营业务挂载的产品品类、材料品类、设备品类、渠道或应用场景节点。
3. L2 以下只保留 contains 分类隶属关系，不要输出 upstream_downstream；L0-L1 的上下游关系仅用于判断一级环节相对行业根节点的上游/下游属性。
4. 不要大规模重写，不要扩展到当前阶段以外的范围；优先保持同一父节点下兄弟节点粒度一致，并修正分支过浅或过深的问题。
5. 保留已有 URL 来源；新增节点或关系必须沿用能支持该判断的已有来源。
6. 输出严格 JSON 图谱，不要 Markdown 或解释文字。
7. 如果当前阶段是一级骨架，修正后必须继续至少保留 1 个 chain_position=upstream 和 1 个 chain_position=downstream 的 L1；行业分类归属不同不能作为删除重要上下游节点的理由。

行业分类蓝图（用于约束行业边界、主分类轴和排除项）：
{json.dumps(blueprint or {}, ensure_ascii=False)}

行业：{industry_name}
评估意见：
{json.dumps(evaluation, ensure_ascii=False)}

当前图谱：
{json.dumps(graph, ensure_ascii=False)}
""".strip()


def _call_quality_json(prompt: str, purpose: str) -> tuple[dict[str, Any], str]:
    response = call_bailian_responses(prompt, purpose, use_search_tools=False)
    raw_text = _response_text(response)
    try:
        return _extract_json_object(raw_text), raw_text
    except BailianAgentError as exc:
        return {
            "status": "needs_revision",
            "score": 0,
            "summary": "质量评估返回内容不是可解析 JSON，已保留原始响应供人工复核。",
            "opinions": ["质量评估 JSON 解析失败，不能自动判定本阶段产业链分类质量。"],
            "revision_focus": ["请人工查看 evaluation_raw_response，确认是否需要重跑或手动调整。"],
            "parse_error": True,
            "parse_error_message": str(exc),
        }, raw_text


def evaluate_seed_graph(
    industry_name: str,
    seed_graph: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    payload = {"classification_blueprint": blueprint or {}, "seed_graph": _compact_graph(seed_graph)}
    prompt = _quality_eval_prompt("一级骨架评估", industry_name, payload)
    result, raw_text = _call_quality_json(prompt, "一级骨架质量评估")
    return result, raw_text, prompt


def revise_seed_graph(
    industry_id: str,
    industry_name: str,
    seed_graph: dict[str, Any],
    evaluation: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    prompt = _revision_prompt("一级骨架", industry_id, industry_name, seed_graph, evaluation, blueprint)
    revised, raw_text = _call_quality_json(prompt, "一级骨架质量修正")
    return standardize_graph(revised, industry_id), raw_text, prompt


def evaluate_branch_graph(
    industry_name: str,
    seed_graph: dict[str, Any],
    branch_node: dict[str, Any],
    branch_graph: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    payload = {
        "classification_blueprint": blueprint or {},
        "level_one_context": _compact_graph(seed_graph),
        "branch_node": _compact_node(branch_node),
        "branch_graph": _compact_graph(branch_graph),
    }
    prompt = _quality_eval_prompt("单分支评估", industry_name, payload)
    result, raw_text = _call_quality_json(prompt, f"分支质量评估 {branch_node.get('name', '')}")
    return result, raw_text, prompt


def revise_branch_graph(
    industry_id: str,
    industry_name: str,
    branch_node: dict[str, Any],
    branch_graph: dict[str, Any],
    evaluation: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    prompt = _revision_prompt(
        f"分支 {branch_node.get('name', '')}",
        industry_id,
        industry_name,
        branch_graph,
        evaluation,
        blueprint,
    )
    revised, raw_text = _call_quality_json(prompt, f"分支质量修正 {branch_node.get('name', '')}")
    return standardize_graph(revised, industry_id), raw_text, prompt


def evaluation_passed(evaluation: dict[str, Any]) -> bool:
    status = str(evaluation.get("status", "")).lower()
    try:
        score_passed = int(evaluation.get("score", 0)) >= 80
    except (TypeError, ValueError):
        return False
    blocking_issues = evaluation.get("blocking_issues") or []
    revision_focus = evaluation.get("revision_focus") or []
    return status == "pass" and score_passed and not blocking_issues and not revision_focus





