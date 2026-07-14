
from __future__ import annotations

import json
import re
from typing import Any

from tools.agent.common import PROJECT_ROOT, content_hash, now_iso
from tools.agent.bailian_client import BailianAgentError, call_bailian_responses

INVESTMENT_RESEARCH_NODE_POLICY = """
投研产业链节点口径：节点应是可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元；优先抽取上游资源/原材料/关键材料/核心零部件、稳定产品或服务品类、专用设备/基础设施、下游渠道/应用/需求场景、必要物流/检测/认证/运维等细分环节；不要把公司/品牌/股票/财务指标、新闻事件、政策/报告标题、市场规模/趋势、消费者画像、泛咨询服务、平台能力、SaaS/解决方案/体系/网络、工艺流程步骤等概念作为节点；同一父节点下兄弟节点粒度必须一致；未来会接入公司节点，因此不要设计会让同一公司挂到多个连续工艺步骤的节点，生产制造类内容应优先落到产品品类、材料品类、设备品类、渠道或应用场景。
""".strip()


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = getattr(item, "content", None)
        if isinstance(item, dict):
            content = item.get("content")
        for part in content or []:
            text = getattr(part, "text", None)
            if isinstance(part, dict):
                text = part.get("text")
            if text:
                chunks.append(str(text))
    if chunks:
        return "\n".join(chunks)

    if hasattr(response, "model_dump_json"):
        return response.model_dump_json(indent=2)
    return str(response)


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def _json_object_candidates(text: str) -> list[str]:
    cleaned = _strip_json_fences(text)
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(cleaned):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(cleaned[start : index + 1])
                start = None
    if candidates:
        return candidates
    start_index = cleaned.find("{")
    end_index = cleaned.rfind("}")
    if start_index != -1 and end_index > start_index:
        return [cleaned[start_index : end_index + 1]]
    return []


def _repair_json_text(text: str) -> str:
    repaired = _strip_json_fences(text)
    repaired = repaired.replace("“", '"').replace("”", '"')
    repaired = repaired.replace("，", ",").replace("：", ":")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(
        r'("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null|\]|\})\s*\n\s*(")',
        r"\1,\n\2",
        repaired,
    )
    return repaired


def _extract_json_object(text: str) -> dict[str, Any]:
    candidates = _json_object_candidates(text)
    if not candidates:
        raise BailianAgentError("Qwen response did not contain a JSON object.")

    errors: list[str] = []
    for candidate in candidates:
        for current in (candidate, _repair_json_text(candidate)):
            try:
                return json.loads(current)
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
    preview = _strip_json_fences(text)[:500].replace("\n", "\\n")
    raise BailianAgentError(f"Qwen response JSON parse failed: {errors[-1]}; preview={preview}") from None


def build_bailian_search_prompt(industry_id: str, industry_name: str, target_depth: str) -> str:
    return f"""
你是一个面向证券/金融投研场景的产业链图谱构建 Agent。请你必须联网搜索券商研究、行业深度报告、行业协会和权威公开资料，为“{industry_name}”构建一个可服务投研分析的标清产业链图谱。

这张图谱用于理解上游成本、中游制造、下游渠道/需求、配套支撑之间的产业传导关系，不是企业名录、资讯摘要或泛百科分类。

{INVESTMENT_RESEARCH_NODE_POLICY}

硬性要求：
1. 必须使用联网搜索和网页抽取能力，不要只依赖模型内部知识。
2. 不要抽取公司节点，不要输出公司列表，不要涉及股票代码、财务指标或个股信息。
3. 图谱只允许两类关系：contains 和 upstream_downstream。
4. contains 的方向是 父节点 -> 子节点；upstream_downstream 的方向是 上游节点 -> 下游节点。
5. 每个节点和每条关系都必须保留至少 1 个 URL 来源。
6. 层级必须使用数字 level 表达，构建目标为：{target_depth}。level=0 是行业根节点；level=1 是一级投研产业链环节；level=2/3/4... 是逐级细分的上游供给、关键材料/零部件、设备/基础设施、产品或服务形态、下游应用/需求等稳定产业分类节点。
7. 不要把 level 简单命名为“上游/中游/下游”。上游/中游/下游/支持只用于 chain_position 或 chain_segment，表示节点在产业链中的位置，不代表层级深度。
8. 每个 L2 及以下节点都必须且只能有一个 parent_id，并且只输出一条与 parent_id 完全一致的 contains 父子关系，不要让同一节点挂到多个父节点；整体目标是 L0-L5 的 5-6 层图谱。多数一级分支至少展开到 L3，核心供给、关键材料、核心零部件、专用设备、重要产品或服务应到 L4，少数稳定品类可到 L5；渠道/物流/检测/运维/咨询等支撑环节通常止于 L3-L4，不要硬凑但也不能全图止于 L3。
9. 图谱要兼顾深度和广度：目标节点数量为 100 个以上，不设硬上限，但不要用低价值概念堆节点；不要只沿少数分支深挖成一条深链，至少应有若干核心 contains 路径达到 L4。
10. level=1 应覆盖该行业主要投研分析环节，通常不少于 5 个；每个重要一级环节应尽量展开 3-8 个二级/三级分支；同一分支继续深挖时要保证兄弟节点也有合理覆盖和一致粒度。
11. 若节点数低于 100，优先补充横向缺失的上游资源/原材料/关键材料/核心零部件、专用设备/基础设施、产品或服务形态、下游应用/需求和必要支撑服务等节点，而不是重复拆分同一概念、拆工艺流程步骤或引入泛服务/平台能力。
12. 关系语义固定：contains 表示分类隶属；upstream_downstream 只允许存在于 L0 和 L1 之间，用于判断一级环节相对行业根节点属于上游还是下游。L2 以下不要输出 upstream_downstream；若两个节点之间已有 contains/SUBORDINATE_TO 关系，就不要再输出 upstream_downstream/DOWNSTREAM_OF。
13. L0-L1 关系规则：chain_position=upstream 的 L1 parent_id 留空，输出 upstream_downstream：L1 -> L0；chain_position=downstream 的 L1 parent_id 留空，输出 upstream_downstream：L0 -> L1；midstream/support 等非上游下游 L1 parent_id 填行业根节点 id，输出 contains：L0 -> L1。同一 L0-L1 节点对不要同时输出两种关系。
14. 禁止把单个工艺流程步骤作为节点。请优先使用品类、材料、设备、渠道、应用场景、消费/需求类别等能承接公司主营业务的节点。
15. 输出必须是严格 JSON，不要输出 Markdown、解释文字或代码块。

请返回如下 JSON 结构：
{{
  "industry": "{industry_name}",
  "version": "v0.1-agent-search",
  "schema_version": "standard_industry_graph_v0.2_agent",
  "generated_at": "",
  "scope": "面向证券/金融投研的标清产业链图谱；目标 100 个以上节点；L2 以下只表达分类隶属；不包含公司节点、股票代码、财务指标、新闻政策、市场趋势、泛服务平台概念或工艺流程节点。",
  "source_basis": [{{"name": "资料标题或机构名称", "url": "https://...", "note": "该来源支持的产业链判断"}}],
  "nodes": [{{
    "id": "{industry_id}_001",
    "name": "节点名称",
    "node_type": "产业链/一级环节/二级环节/细分环节/原材料/材料/产品/设备/渠道/应用场景/支撑服务",
    "tags": ["level_0", "root"],
    "industry": "{industry_name}",
    "level": 0,
    "chain_position": "root/upstream/midstream/downstream/support",
    "parent_id": "",
    "description": "一句话业务描述",
    "business_description": "一句话业务描述",
    "is_key_node": true,
    "chain_segment": "root/上游/中游/下游/支持（位置标签，不是层级名称）",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}],
  "edges": [{{
    "source": "{industry_id}_001",
    "target": "{industry_id}_002",
    "relation_type": "contains",
    "relation_weight": 1.0,
    "description": "关系说明",
    "source_urls": ["https://..."],
    "evidence_ids": ["{industry_id}_ev_0001"],
    "confidence": 0.85,
    "updated_at": ""
  }}]
}}
""".strip()


def call_bailian_search_agent(industry_id: str, industry_name: str, target_depth: str = "5-6 层，100 个以上节点，不设硬上限但避免低价值堆节点") -> tuple[dict[str, Any], str]:
    response = call_bailian_responses(
        build_bailian_search_prompt(industry_id, industry_name, target_depth),
        "联网搜索构建",
    )
    raw_text = _response_text(response)
    return _extract_json_object(raw_text), raw_text

def evidence_from_agent_graph(industry_id: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for source in graph.get("source_basis", []) or []:
        url = source.get("url")
        if url:
            seen[url] = {"title": source.get("name", ""), "snippet": source.get("note", "")}
    for item in list(graph.get("nodes", []) or []) + list(graph.get("edges", []) or []):
        for url in item.get("source_urls", []) or []:
            seen.setdefault(url, {"title": "", "snippet": item.get("description", "")})

    retrieved_at = now_iso()
    rows = []
    for index, (url, meta) in enumerate(seen.items(), start=1):
        snippet = meta.get("snippet", "")
        rows.append(
            {
                "evidence_id": f"{industry_id}_ev_{index:04d}",
                "url": url,
                "title": meta.get("title", ""),
                "published_at": "",
                "retrieved_at": retrieved_at,
                "content_type": "web_search_result",
                "content_hash": content_hash(url + snippet),
                "snippet": snippet,
                "status": "ok",
            }
        )
    return rows









