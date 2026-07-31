from __future__ import annotations

import unittest
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.agent.company_attachments import (
    aggregate_node_companies,
    ancestors_for_node,
    descendants_for_node,
    filter_domestic_listed_companies,
    filter_listed_attachments,
    graph_fingerprint,
    is_listed_company,
    prune_graph_to_company_coverage,
)
from tools.agent.export_csv import export_company_attachment_csv, export_graph_csv


def company(company_id: str, is_listed, is_abroad_listed=None) -> dict:
    return {
        "company_id": company_id,
        "comcode": company_id.removeprefix("sw_"),
        "name": f"{company_id}股份有限公司",
        "short_name": company_id,
        "is_listed": is_listed,
        "is_abroad_listed": is_abroad_listed,
        "sw_industry": {"indunamesw": "软件开发"},
    }


def attachment(company_id: str, node_id: str) -> dict:
    return {
        "company_id": company_id,
        "node_id": node_id,
        "reason": "主营匹配",
        "confidence": 0.9,
        "match_method": "agent_web_search",
    }


SAMPLE_PAYLOAD = {
    "schema_version": "industry_company_attachments_v0.2",
    "industry_id": "food_beverage",
    "graph_fingerprint": "fingerprint",
    "candidate_source": {"path": "x.csv", "sha256": "abc"},
    "scope": {"rules": [{"column": "indunamesw", "values": ["软件开发"]}]},
    "companies": [
        company("sw_listed", True),
        company("sw_abroad", None, True),
        company("sw_abroad_explicit_false", False, True),
        company("sw_unlisted", False),
        company("sw_blank", None),
    ],
    "attachments": [
        attachment("sw_listed", "food_beverage_001"),
        attachment("sw_abroad", "food_beverage_001"),
        attachment("sw_abroad_explicit_false", "food_beverage_002"),
        attachment("sw_unlisted", "food_beverage_001"),
        attachment("sw_blank", "food_beverage_002"),
    ],
}

SAMPLE_GRAPH = {
    "schema_version": "industry_graph_v0.2",
    "industry": "测试行业",
    "nodes": [
        {"id": "food_beverage_000", "name": "测试行业", "level": 0, "chain_position": "root", "parent_id": None},
        {"id": "food_beverage_001", "name": "环节一", "level": 1, "chain_position": "upstream", "parent_id": "food_beverage_000"},
        {"id": "food_beverage_002", "name": "环节二", "level": 1, "chain_position": "midstream", "parent_id": "food_beverage_000"},
    ],
    "edges": [],
}


class ListedCompanyPredicateTest(unittest.TestCase):
    def test_explicit_domestic_listing_counts(self):
        self.assertTrue(is_listed_company(company("sw_a", True)))

    def test_abroad_only_listing_is_excluded(self):
        self.assertFalse(is_listed_company(company("sw_a", None, True)))

    def test_blank_flag_is_treated_as_unlisted(self):
        # 源 CSV 中 islisted 为空表示非上市主体（集团母公司、子公司），不是缺失数据。
        self.assertFalse(is_listed_company(company("sw_a", None)))

    def test_explicit_false_is_unlisted(self):
        self.assertFalse(is_listed_company(company("sw_a", False)))

    def test_model_candidate_filter_keeps_only_domestic_listed_companies(self):
        filtered = filter_domestic_listed_companies(SAMPLE_PAYLOAD["companies"])
        self.assertEqual([item["company_id"] for item in filtered], ["sw_listed"])


class FilterListedAttachmentsTest(unittest.TestCase):
    def test_keeps_only_listed_companies_and_their_attachments(self):
        filtered, stats = filter_listed_attachments(SAMPLE_PAYLOAD)
        self.assertEqual(
            sorted(item["company_id"] for item in filtered["companies"]),
            ["sw_listed"],
        )
        self.assertEqual(
            sorted(item["company_id"] for item in filtered["attachments"]),
            ["sw_listed"],
        )
        self.assertEqual(stats["company_count_before"], 5)
        self.assertEqual(stats["company_count_after"], 1)
        self.assertEqual(stats["attachment_removed"], 4)
        self.assertEqual(stats["listed_domestic_count"], 1)
        self.assertEqual(stats["listed_abroad_only_count"], 2)
        self.assertEqual(stats["removed_flag_counts"], {"is_listed=false": 2, "is_listed=null": 2})

    def test_preserves_metadata_required_by_attachment_file_status(self):
        filtered, _ = filter_listed_attachments(SAMPLE_PAYLOAD)
        for key in ("schema_version", "industry_id", "graph_fingerprint", "candidate_source", "scope"):
            self.assertEqual(filtered[key], SAMPLE_PAYLOAD[key])

    def test_is_idempotent(self):
        once, _ = filter_listed_attachments(SAMPLE_PAYLOAD)
        twice, stats = filter_listed_attachments(once)
        self.assertEqual(once["companies"], twice["companies"])
        self.assertEqual(once["attachments"], twice["attachments"])
        self.assertEqual(stats["company_removed"], 0)

    def test_does_not_mutate_input(self):
        filter_listed_attachments(SAMPLE_PAYLOAD)
        self.assertEqual(len(SAMPLE_PAYLOAD["companies"]), 5)
        self.assertEqual(len(SAMPLE_PAYLOAD["attachments"]), 5)

    def test_drops_companies_left_without_attachments(self):
        payload = {**SAMPLE_PAYLOAD, "attachments": [attachment("sw_listed", "food_beverage_001")]}
        filtered, _ = filter_listed_attachments(payload)
        self.assertEqual([item["company_id"] for item in filtered["companies"]], ["sw_listed"])


class ExportCompanyCsvListedOnlyTest(unittest.TestCase):
    def _rows(self, listed_only: bool) -> list[str]:
        with TemporaryDirectory() as directory:
            result = export_company_attachment_csv(
                SAMPLE_GRAPH, SAMPLE_PAYLOAD, "food_beverage", Path(directory), listed_only=listed_only
            )
            content = Path(result["company_node_csv"]).read_text(encoding="utf-8-sig")
        return [line for line in content.splitlines()[1:] if line.strip()]

    def test_defaults_to_listed_only(self):
        self.assertEqual(len(self._rows(listed_only=True)), 1)

    def test_include_unlisted_exports_everything(self):
        self.assertEqual(len(self._rows(listed_only=False)), 5)


class CompanyAggregationTest(unittest.TestCase):
    GRAPH = {
        "schema_version": "industry_graph_v0.2",
        "industry": "测试行业",
        "nodes": [
            {"id": "food_beverage_000", "name": "测试行业", "level": 0, "chain_position": "root", "parent_id": None},
            {"id": "food_beverage_001", "name": "上游环节", "level": 1, "chain_position": "upstream", "parent_id": None},
            {"id": "food_beverage_002", "name": "上游子分类", "level": 2, "chain_position": "upstream", "parent_id": "food_beverage_001"},
            {"id": "food_beverage_003", "name": "非隶属流向节点", "level": 2, "chain_position": "midstream", "parent_id": None},
        ],
        "edges": [
            {"id": "upstream_to_root", "source": "food_beverage_001", "target": "food_beverage_000", "relation_type": "upstream_downstream"},
            {"id": "contains_l2", "source": "food_beverage_001", "target": "food_beverage_002", "relation_type": "contains"},
            {"id": "l1_to_l2_flow", "source": "food_beverage_001", "target": "food_beverage_003", "relation_type": "upstream_downstream"},
        ],
    }
    ATTACHMENTS = {
        "companies": [company("sw_listed", True)],
        "attachments": [attachment("sw_listed", "food_beverage_001")],
    }

    def test_only_l0_l1_flow_extends_the_aggregation_hierarchy(self):
        self.assertEqual(
            descendants_for_node(self.GRAPH, "food_beverage_000"),
            {"food_beverage_000", "food_beverage_001", "food_beverage_002"},
        )
        self.assertEqual(ancestors_for_node(self.GRAPH, "food_beverage_002"), ["food_beverage_001", "food_beverage_000"])
        self.assertEqual(ancestors_for_node(self.GRAPH, "food_beverage_003"), [])

    def test_query_and_csv_export_include_aggregation_parents(self):
        aggregated = aggregate_node_companies(self.GRAPH, self.ATTACHMENTS, "food_beverage_000")
        self.assertEqual([item["company_id"] for item in aggregated], ["sw_listed"])
        with TemporaryDirectory() as directory:
            result = export_graph_csv(
                self.GRAPH, "food_beverage", Path(directory), company_attachments=self.ATTACHMENTS
            )
            company_result = export_company_attachment_csv(
                self.GRAPH, self.ATTACHMENTS, "food_beverage", Path(directory)
            )
            with Path(result["industrynode_node_csv"]).open(encoding="utf-8-sig", newline="") as file:
                node_rows = list(csv.DictReader(file))
            with Path(company_result["company_edge_csv"]).open(encoding="utf-8-sig", newline="") as file:
                company_edges = list(csv.DictReader(file))
        root_row = next(row for row in node_rows if row["节点id"] == "FOOD000000")
        self.assertEqual(root_row["公司列表"], "sw_listed股份有限公司")
        self.assertEqual(
            {row["终点节点id"] for row in company_edges},
            {"FOOD000000", "FOOD000001"},
        )

    def test_csv_export_keeps_company_attached_singleton_leaf(self):
        attachments = {
            "companies": [company("sw_listed", True)],
            "attachments": [attachment("sw_listed", "food_beverage_002")],
        }
        with TemporaryDirectory() as directory:
            result = export_graph_csv(self.GRAPH, "food_beverage", Path(directory), company_attachments=attachments)
            with Path(result["industrynode_node_csv"]).open(encoding="utf-8-sig", newline="") as file:
                node_rows = list(csv.DictReader(file))
        self.assertIn("FOOD000002", {row["节点id"] for row in node_rows})

    def test_pruning_keeps_direct_nodes_and_all_aggregation_parents(self):
        attachments = {
            "companies": [company("sw_listed", True)],
            "attachments": [attachment("sw_listed", "food_beverage_002")],
        }
        pruned_graph, rebased_attachments, report = prune_graph_to_company_coverage(self.GRAPH, attachments)
        self.assertEqual(
            {node["id"] for node in pruned_graph["nodes"]},
            {"food_beverage_000", "food_beverage_001", "food_beverage_002"},
        )
        self.assertEqual(report["removed_nodes"], [{"id": "food_beverage_003", "name": "非隶属流向节点", "level": 2}])
        self.assertEqual(rebased_attachments["graph_fingerprint"], graph_fingerprint(pruned_graph))


if __name__ == "__main__":
    unittest.main()
