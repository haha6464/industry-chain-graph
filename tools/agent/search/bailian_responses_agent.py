
from __future__ import annotations

import json
from typing import Any

from tools.agent.common import PROJECT_ROOT, content_hash, now_iso
from tools.agent.bailian_client import BailianAgentError, call_bailian_responses

INVESTMENT_RESEARCH_NODE_POLICY = """
投研产业链节点口径：节点应是可用于行业比较、成本拆解、上下游传导、公司业务归因的稳定产业分类单元；优先抽取上游资源/原材料/关键材料/核心零部件、生产制造或服务交付、关键工艺/技术路线、专用设备/基础设施、产品或服务形态、下游应用/需求场景、必要渠道/物流/检测/认证/运维等细分环节；不要把公司/品牌/股票/财务指标、新闻事件、政策/报告标题、市场规模/趋势、消费者画像、泛咨询服务、平台能力、SaaS/解决方案/体系/网络等概念作为节点；同一父节点下兄弟节点粒度必须一致。
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


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise BailianAgentError("Qwen response did not contain a JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BailianAgentError(f"Qwen response JSON parse failed: {exc}") from exc


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
6. 层级必须使用数字 level 表达，构建目标为：{target_depth}。level=0 是行业根节点；level=1 是一级投研产业链环节；level=2/3/4... 是逐级细分的上游供给、关键材料/零部件、工艺/技术路线、设备/基础设施、产品或服务形态、下游应用/需求等稳定产业分类节点。
7. 不要把 level 简单命名为“上游/中游/下游”。上游/中游/下游/支持只用于 chain_position 或 chain_segment，表示节点在产业链中的位置，不代表层级深度。
8. 每个非根节点都应通过 parent_id 和 contains 关系挂到上一级节点；多数一级分支展开到 L3，核心供给/生产转换/关键材料/核心零部件/专用设备/重要产品或服务可到 L4，渠道/物流/检测/运维/咨询等支撑环节通常止于 L2-L3，不要硬凑层级。
9. 图谱要兼顾深度和广度：目标节点数量为 60-100 个，硬上限 150 个；不要只沿少数分支深挖成一条深链。
10. level=1 应覆盖该行业主要投研分析环节，通常不少于 5 个；每个重要一级环节应尽量展开 3-8 个二级/三级分支；同一分支继续深挖时要保证兄弟节点也有合理覆盖和一致粒度。
11. 若节点数低于 60，优先补充横向缺失的上游资源/原材料/关键材料/核心零部件、关键工艺/技术路线、专用设备/基础设施、产品或服务形态、下游应用/需求和必要支撑服务等节点，而不是重复拆分同一概念或引入泛服务/平台能力。
12. upstream_downstream 只表示不同节点之间的流向关系，不替代 contains 层级关系。
13. 输出必须是严格 JSON，不要输出 Markdown、解释文字或代码块。

请返回如下 JSON 结构：
{{
  "industry": "{industry_name}",
  "version": "v0.1-agent-search",
  "schema_version": "standard_industry_graph_v0.2_agent",
  "generated_at": "",
  "scope": "面向证券/金融投研的标清产业链图谱；目标 60-100 个节点，硬上限 150 个节点；不包含公司节点、股票代码、财务指标、新闻政策、市场趋势或泛服务平台概念。",
  "source_basis": [{{"name": "资料标题或机构名称", "url": "https://...", "note": "该来源支持的产业链判断"}}],
  "nodes": [{{
    "id": "{industry_id}_001",
    "name": "节点名称",
    "node_type": "产业链/一级环节/二级环节/细分环节/原材料/产品/工艺/渠道/应用场景",
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


def call_bailian_search_agent(industry_id: str, industry_name: str, target_depth: str = "5-6 层，60-100 个节点，最多 150 个节点") -> tuple[dict[str, Any], str]:
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



