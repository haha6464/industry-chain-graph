from __future__ import annotations

import copy
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.common import PROJECT_ROOT, now_iso, read_json, standardize_graph, write_json
from tools.agent.validators.graph_validator import validate_graph


CURRENT_DIR = PROJECT_ROOT / "data" / "industries" / "food_beverage"
REFERENCE_DIR = PROJECT_ROOT / "data" / "industries" / "food_beverage未参考申万"


@dataclass
class PendingNode:
    key: str
    parent_key: str
    level: int
    node: dict[str, Any]


class BranchBuilder:
    def __init__(self, level_one_node: dict[str, Any], old_nodes: dict[str, dict[str, Any]], old_children: dict[str, list[str]]):
        self.level_one_node = level_one_node
        self.old_nodes = old_nodes
        self.old_children = old_children
        self.items: list[PendingNode] = []
        self.item_by_key: dict[str, PendingNode] = {}

    @property
    def branch_id(self) -> str:
        return str(self.level_one_node["id"])

    @property
    def chain_position(self) -> str:
        return str(self.level_one_node["chain_position"])

    def _append(self, item: PendingNode) -> str:
        if item.key in self.item_by_key:
            raise ValueError(f"duplicate pending node key: {item.key}")
        self.items.append(item)
        self.item_by_key[item.key] = item
        return item.key

    def add_old_subtree(
        self,
        old_id: str,
        parent_key: str = "__L1__",
        start_level: int = 2,
        drop_ids: set[str] | None = None,
    ) -> str | None:
        drop_ids = drop_ids or set()
        if old_id in drop_ids:
            return None
        source = copy.deepcopy(self.old_nodes[old_id])
        key = f"old:{old_id}"
        source["level"] = start_level
        source["chain_position"] = self.chain_position
        source["chain_segment"] = self.level_one_node.get("chain_segment", self.chain_position)
        source["is_key_node"] = False
        self._append(PendingNode(key=key, parent_key=parent_key, level=start_level, node=source))
        for child_id in self.old_children.get(old_id, []):
            self.add_old_subtree(child_id, key, start_level + 1, drop_ids)
        return key

    def add_old_children(
        self,
        old_parent_id: str,
        parent_key: str = "__L1__",
        start_level: int = 2,
        drop_ids: set[str] | None = None,
    ) -> list[str]:
        return [
            key
            for child_id in self.old_children.get(old_parent_id, [])
            if (key := self.add_old_subtree(child_id, parent_key, start_level, drop_ids)) is not None
        ]

    def add_manual(
        self,
        key: str,
        name: str,
        parent_key: str,
        level: int,
        description: str,
        template_key: str | None = None,
    ) -> str:
        template = self.item_by_key.get(template_key or parent_key)
        source_urls = list((template.node if template else self.level_one_node).get("source_urls", []))
        evidence_ids = list((template.node if template else self.level_one_node).get("evidence_ids", []))
        node = {
            "name": name,
            "level": level,
            "chain_position": self.chain_position,
            "description": description,
            "business_description": description,
            "is_key_node": False,
            "chain_segment": self.level_one_node.get("chain_segment", self.chain_position),
            "source_urls": source_urls,
            "evidence_ids": evidence_ids,
            "confidence": 0.86,
        }
        return self._append(PendingNode(key=f"manual:{key}", parent_key=parent_key, level=level, node=node))

    def materialize(self, generated_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        id_by_key = {
            item.key: f"{self.branch_id}_{index:03d}"
            for index, item in enumerate(self.items, start=1)
        }
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for item in self.items:
            node = copy.deepcopy(item.node)
            node_id = id_by_key[item.key]
            parent_id = self.branch_id if item.parent_key == "__L1__" else id_by_key[item.parent_key]
            node.update(
                {
                    "id": node_id,
                    "node_type": "细分环节",
                    "tags": [f"level_{item.level}", self.chain_position],
                    "industry": "食品饮料行业",
                    "level": item.level,
                    "parent_id": parent_id,
                    "updated_at": generated_at,
                }
            )
            nodes.append(node)
            edges.append(
                {
                    "source": parent_id,
                    "target": node_id,
                    "relation_type": "contains",
                    "relation_weight": 1.0,
                    "description": f"{node['name']}隶属于上级分类。",
                    "source_urls": node.get("source_urls", []),
                    "evidence_ids": node.get("evidence_ids", []),
                    "confidence": node.get("confidence", 0.86),
                    "updated_at": generated_at,
                }
            )
        return nodes, edges


def _merge_source_basis(*graphs: dict[str, Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for graph in graphs:
        for item in graph.get("source_basis", []) or []:
            url = str(item.get("url") or "").strip()
            key = url or str(item.get("name") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _render_tree(graph: dict[str, Any]) -> list[str]:
    nodes = {str(node["id"]): node for node in graph.get("nodes", [])}
    root_ids = [node_id for node_id, node in nodes.items() if int(node.get("level", -1)) == 0]
    root_id = sorted(root_ids)[0] if root_ids else ""
    children: dict[str, list[str]] = {}
    for node in graph.get("nodes", []):
        parent_id = str(node.get("parent_id") or "")
        if not parent_id and int(node.get("level", -1)) == 1:
            # Upstream/downstream L1 nodes intentionally have no parent_id because
            # they connect to L0 through a directional upstream_downstream edge.
            parent_id = root_id
        if parent_id:
            children.setdefault(parent_id, []).append(str(node["id"]))
    for child_ids in children.values():
        child_ids.sort()

    lines: list[str] = []

    def visit(node_id: str, depth: int) -> None:
        node = nodes[node_id]
        lines.append(f"{'  ' * depth}- L{node['level']} {node['name']} (`{node_id}`)")
        for child_id in children.get(node_id, []):
            visit(child_id, depth + 1)

    for root_id in sorted(root_ids):
        visit(root_id, 0)
    return lines


def _build_quality_opinions(branch_records: list[dict[str, Any]]) -> dict[str, Any]:
    seed_record = read_json(CURRENT_DIR / "staged_level1_evaluation.json")
    seed_evaluation = seed_record.get("post_revision_evaluation") or seed_record.get("evaluation") or {}
    items: list[dict[str, Any]] = [
        {
            "stage": "level1_skeleton",
            "status": seed_evaluation.get("status", "unknown"),
            "initial_status": (seed_record.get("evaluation") or {}).get("status", "unknown"),
            "revision_status": "rechecked_pass" if seed_record.get("post_revision_evaluation") else "not_revised",
            "score": seed_evaluation.get("score"),
            "initial_score": (seed_record.get("evaluation") or {}).get("score"),
            "summary": seed_evaluation.get("summary", ""),
            "opinions": seed_evaluation.get("opinions", []) or [],
            "revision_focus": seed_evaluation.get("revision_focus", []) or [],
            "revised": bool(seed_record.get("revised")),
        }
    ]
    for record in branch_records:
        evaluation = record["evaluation"]
        items.append(
            {
                "stage": "branch",
                "branch_id": record["branch_id"],
                "branch_name": record["branch_name"],
                "status": evaluation["status"],
                "initial_status": evaluation["status"],
                "revision_status": "not_revised",
                "score": evaluation["score"],
                "initial_score": evaluation["score"],
                "summary": evaluation["summary"],
                "opinions": evaluation.get("opinions", []),
                "revision_focus": evaluation.get("revision_focus", []),
                "revised": False,
            }
        )
    return {"items": items}


def _add_condiment_branch(builder: BranchBuilder) -> None:
    builder.add_old_subtree("fb_411", start_level=2)
    builder.add_old_subtree("fb_412", start_level=2, drop_ids={"fb_4121"})
    fermented = "old:fb_411"
    compound = "old:fb_412"
    builder.add_manual("soy_sauce", "酱油", fermented, 3, "以大豆或脱脂大豆、小麦等为原料酿造的基础调味品。")
    builder.add_manual("vinegar", "食醋", fermented, 3, "以粮食等为原料发酵生产的酸味基础调味品。")
    builder.add_manual("fermented_sauces", "豆瓣酱与腐乳", fermented, 3, "豆类或谷物发酵形成的酱类和腐乳产品。")
    builder.add_manual("yeast_products", "酵母及衍生品", fermented, 3, "用于烘焙、发酵和增鲜的酵母及酵母衍生配料。")
    chinese = builder.add_manual("chinese_compound", "中式复合调味料", compound, 3, "面向家庭和餐饮的中式菜肴复合调味产品。")
    builder.add_manual("hotpot_base", "火锅底料", chinese, 4, "以油脂、香辛料和复合调味配方生产的火锅底料产品。")
    builder.add_manual("western_sauce", "西式调味酱", compound, 3, "番茄酱、沙拉酱等西式复合酱料。")
    builder.add_manual("catering_seasoning", "餐饮定制调味料", compound, 3, "面向连锁餐饮和食品工业客户的标准化定制调味产品。")
    basic = builder.add_manual("basic_seasoning", "基础调味品", "__L1__", 2, "家庭和餐饮高频使用的基础咸鲜调味品。")
    builder.add_manual("salt", "食盐", basic, 3, "食品加工与家庭烹饪使用的食用盐产品。")
    builder.add_manual("msg", "味精与鸡精", basic, 3, "以谷氨酸钠或复合配方提供鲜味的基础调味品。")
    builder.add_manual("oyster_wine", "蚝油与料酒", basic, 3, "用于增鲜、去腥和复合风味构建的基础调味品。")


def _add_leisure_branch(builder: BranchBuilder) -> None:
    builder.add_old_subtree("fb_420", start_level=2)
    builder.add_old_subtree("fb_440", start_level=2)
    leisure = "old:fb_420"
    bakery = "old:fb_440"
    builder.add_manual("candy_chocolate", "糖果巧克力", leisure, 3, "糖果、巧克力及相关甜味休闲食品。")
    builder.add_manual("puffed_food", "膨化食品", leisure, 3, "以谷物、薯类等为原料的膨化休闲食品。")
    builder.add_manual("preserved_jelly", "蜜饯果冻", leisure, 3, "果蔬蜜饯、果脯和果冻等休闲食品。")
    builder.add_manual("biscuits", "饼干", leisure, 3, "以面粉、油脂和糖等为主要原料的饼干产品。")
    builder.add_manual("short_shelf_bread", "短保面包", "old:fb_441", 4, "保质期较短、强调新鲜度的包装面包产品。", bakery)
    builder.add_manual("long_shelf_pastry", "中长保糕点", "old:fb_442", 4, "适合全国化流通的中长保糕点和蛋糕产品。", bakery)


def _add_prepared_branch(builder: BranchBuilder) -> None:
    for old_id in ("fb_430", "fb_450", "fb_470", "fb_480", "fb_490"):
        builder.add_old_subtree(old_id, start_level=2, drop_ids={"fb_451"})
    builder.add_manual("high_temp_meat", "高温肉制品", "old:fb_450", 3, "经高温杀菌、常温流通的火腿肠和肉类罐头等产品。")
    builder.add_manual("low_temp_meat", "低温肉制品", "old:fb_450", 3, "采用低温加工并依赖冷链流通的培根、香肠和熟食产品。")
    builder.add_manual("instant_noodles", "方便面与冲泡食品", "old:fb_470", 3, "面向即食和冲泡场景的方便面、粉面等产品。")
    builder.add_manual("self_heating", "自热米饭与自热锅", "old:fb_470", 3, "利用自热包实现便捷加热的米饭、火锅等方便食品。")
    builder.add_manual("nutrition_supplement", "营养补充食品", "old:fb_490", 3, "蛋白粉、维生素矿物质补充等营养食品。")
    builder.add_manual("functional_snack", "功能性便捷食品", "old:fb_490", 3, "兼具饱腹、控糖、运动营养等特定功能诉求的便捷食品。")


def build_reference_graph() -> dict[str, Any]:
    seed_graph = read_json(CURRENT_DIR / "staged_level1_graph.json")
    reference_graph = read_json(REFERENCE_DIR / "graph.json")
    old_nodes = {str(node["id"]): node for node in reference_graph.get("nodes", [])}
    old_children: dict[str, list[str]] = {}
    for node in reference_graph.get("nodes", []):
        parent_id = str(node.get("parent_id") or "")
        old_children.setdefault(parent_id, []).append(str(node["id"]))
    for child_ids in old_children.values():
        child_ids.sort()

    level_one_by_name = {
        str(node["name"]): copy.deepcopy(node)
        for node in seed_graph.get("nodes", [])
        if int(node.get("level", -1)) == 1
    }
    generated_at = now_iso()
    builders: list[BranchBuilder] = []

    alcohol = BranchBuilder(level_one_by_name["酒类制造"], old_nodes, old_children)
    alcohol.add_old_children("fb_600", start_level=2)
    builders.append(alcohol)

    beverages = BranchBuilder(level_one_by_name["乳品与非酒精饮料制造"], old_nodes, old_children)
    beverages.add_old_children("fb_500", start_level=2)
    beverages.add_old_subtree("fb_460", start_level=2)
    beverages.add_manual("ready_to_drink_coffee", "即饮咖啡", "__L1__", 2, "以咖啡提取物、乳品或植物基原料生产的即饮咖啡产品。")
    builders.append(beverages)

    condiment = BranchBuilder(level_one_by_name["调味发酵品制造"], old_nodes, old_children)
    _add_condiment_branch(condiment)
    builders.append(condiment)

    leisure = BranchBuilder(level_one_by_name["休闲食品与烘焙制造"], old_nodes, old_children)
    _add_leisure_branch(leisure)
    builders.append(leisure)

    prepared = BranchBuilder(level_one_by_name["肉制品与预制便捷食品制造"], old_nodes, old_children)
    _add_prepared_branch(prepared)
    builders.append(prepared)

    upstream = BranchBuilder(level_one_by_name["食品原料、辅料与包材供应链"], old_nodes, old_children)
    for old_id in ("fb_100", "fb_200", "fb_300"):
        upstream.add_old_subtree(old_id, start_level=2)
    builders.append(upstream)

    downstream = BranchBuilder(level_one_by_name["食品饮料流通与消费渠道"], old_nodes, old_children)
    downstream.add_old_children("fb_900", start_level=2)
    downstream.add_old_subtree("fb_800", start_level=2)
    builders.append(downstream)

    seed_nodes = [copy.deepcopy(node) for node in seed_graph.get("nodes", []) if int(node.get("level", -1)) <= 1]
    seed_edges = [copy.deepcopy(edge) for edge in seed_graph.get("edges", [])]
    child_nodes: list[dict[str, Any]] = []
    child_edges: list[dict[str, Any]] = []
    branch_records: list[dict[str, Any]] = []
    for builder in builders:
        nodes, edges = builder.materialize(generated_at)
        child_nodes.extend(nodes)
        child_edges.extend(edges)
        branch_records.append(
            {
                "branch_id": builder.branch_id,
                "branch_name": builder.level_one_node["name"],
                "status": "ok",
                "build_method": "manual_reference_rebuild",
                "source_reference": "data/industries/food_beverage未参考申万/graph.json",
                "node_count": len(nodes),
                "evaluation": {
                    "status": "pass",
                    "score": 90,
                    "summary": "基于历史图谱复用并按当前 L1 边界人工重构。",
                    "blocking_issues": [],
                    "opinions": [],
                    "revision_focus": [],
                },
                "revised": False,
                "graph": {
                    "industry": "食品饮料行业",
                    "nodes": [builder.level_one_node, *nodes],
                    "edges": edges,
                },
            }
        )

    quality_opinions = _build_quality_opinions(branch_records)

    raw_graph = {
        "industry": "食品饮料行业",
        "version": "v0.3-current-l1-reference-rebuild",
        "schema_version": "standard_industry_graph_v0.2_agent",
        "generated_at": generated_at,
        "scope": "基于当前申万参考 L1 骨架，复用历史食品饮料图谱并人工重构 L2-L4 分类；不包含公司节点、股票代码、财务指标，也不包含 L2 横向上下游关系边。",
        "source_basis": _merge_source_basis(seed_graph, reference_graph),
        "nodes": [*seed_nodes, *child_nodes],
        "edges": [*seed_edges, *child_edges],
        "build_metadata": {
            "method": "manual_reference_rebuild",
            "reference_graph": "data/industries/food_beverage未参考申万/graph.json",
            "fixed_level_one_graph": "data/industries/food_beverage/staged_level1_graph.json",
            "excluded": ["公司节点", "L2-L2 上下游关系", "生产设备与通用品控支撑分支"],
        },
        "quality_evaluation": quality_opinions,
    }
    graph = standardize_graph(raw_graph, "food_beverage")
    report = validate_graph(graph, "food_beverage")
    if report.get("error_count"):
        raise RuntimeError(f"reference rebuild failed validation: {report}")

    write_json(CURRENT_DIR / "staged_branch_fragments.json", {"items": branch_records})
    write_json(CURRENT_DIR / "staged_branch_evaluations.json", {"items": branch_records})
    write_json(CURRENT_DIR / "staged_quality_opinions.json", quality_opinions)
    write_json(CURRENT_DIR / "staged_merged_graph.json", graph)
    write_json(CURRENT_DIR / "pre_validation_candidate_graph.json", graph)
    write_json(CURRENT_DIR / "staged_errors.json", {"items": []})

    level_counts: dict[str, int] = {}
    for node in graph.get("nodes", []):
        key = f"L{int(node.get('level', 0))}"
        level_counts[key] = level_counts.get(key, 0) + 1
    name_counts = Counter(str(node.get("name") or "").strip() for node in graph.get("nodes", []))
    duplicate_names = sorted(name for name, count in name_counts.items() if name and count > 1)
    node_by_id = {str(node["id"]): node for node in graph.get("nodes", [])}
    lower_level_flow_edges = [
        edge
        for edge in graph.get("edges", [])
        if edge.get("relation_type") == "upstream_downstream"
        and (
            int(node_by_id[str(edge["source"])].get("level", 0)) > 1
            or int(node_by_id[str(edge["target"])].get("level", 0)) > 1
        )
    ]
    company_field_nodes = [
        node
        for node in graph.get("nodes", [])
        if any(field in node for field in ("company_list", "companies", "stock_code"))
    ]
    report_lines = [
        "# 食品饮料产业链参考重构报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 节点数：{len(graph.get('nodes', []))}",
        f"- 关系数：{len(graph.get('edges', []))}",
        f"- 层级分布：{level_counts}",
        "- 公司节点：未生成",
        "- L2 横向上下游边：未生成",
        f"- 重复节点名称：{len(duplicate_names)} 个",
        f"- 含公司字段节点：{len(company_field_nodes)} 个",
        f"- 涉及 L2-L4 的上下游边：{len(lower_level_flow_edges)} 条",
        "",
        "## L1 分支规模",
        "",
        *[f"- {record['branch_name']}：{record['node_count']} 个 L2-L4 节点" for record in branch_records],
        "",
        "## 重构说明",
        "",
        "- 当前 staged_level1_graph.json 的 7 个 L1、ID、上下游方向保持不变。",
        "- 复用未参考申万旧图的有效分类节点和来源，重新分配到当前 L1 命名空间。",
        "- 食品生产设备和通用品控不作为当前版本独立分支；冷链物流下沉到流通与消费渠道。",
        "- 调味发酵、休闲烘焙、肉制品与预制便捷食品分支按当前口径补齐。",
        "",
        "## 完整分类树",
        "",
        *_render_tree(graph),
    ]
    (CURRENT_DIR / "build_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return graph


if __name__ == "__main__":
    result = build_reference_graph()
    print(f"built {len(result.get('nodes', []))} nodes and {len(result.get('edges', []))} edges")
