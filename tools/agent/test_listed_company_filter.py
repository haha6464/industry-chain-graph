from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.agent.company_attachments import filter_listed_attachments, is_listed_company
from tools.agent.export_csv import export_company_attachment_csv


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
    "industry_id": "test",
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
        attachment("sw_listed", "n1"),
        attachment("sw_abroad", "n1"),
        attachment("sw_abroad_explicit_false", "n2"),
        attachment("sw_unlisted", "n1"),
        attachment("sw_blank", "n2"),
    ],
}

SAMPLE_GRAPH = {
    "schema_version": "industry_graph_v0.2",
    "industry": "测试行业",
    "nodes": [
        {"id": "root", "name": "测试行业", "level": 0, "chain_position": "root", "parent_id": None},
        {"id": "n1", "name": "环节一", "level": 1, "chain_position": "upstream", "parent_id": "root"},
        {"id": "n2", "name": "环节二", "level": 1, "chain_position": "midstream", "parent_id": "root"},
    ],
    "edges": [],
}


class ListedCompanyPredicateTest(unittest.TestCase):
    def test_explicit_domestic_listing_counts(self):
        self.assertTrue(is_listed_company(company("sw_a", True)))

    def test_abroad_only_listing_counts(self):
        self.assertTrue(is_listed_company(company("sw_a", None, True)))

    def test_blank_flag_is_treated_as_unlisted(self):
        # 源 CSV 中 islisted 为空表示非上市主体（集团母公司、子公司），不是缺失数据。
        self.assertFalse(is_listed_company(company("sw_a", None)))

    def test_explicit_false_is_unlisted(self):
        self.assertFalse(is_listed_company(company("sw_a", False)))


class FilterListedAttachmentsTest(unittest.TestCase):
    def test_keeps_only_listed_companies_and_their_attachments(self):
        filtered, stats = filter_listed_attachments(SAMPLE_PAYLOAD)
        self.assertEqual(
            sorted(item["company_id"] for item in filtered["companies"]),
            ["sw_abroad", "sw_abroad_explicit_false", "sw_listed"],
        )
        self.assertEqual(
            sorted(item["company_id"] for item in filtered["attachments"]),
            ["sw_abroad", "sw_abroad_explicit_false", "sw_listed"],
        )
        self.assertEqual(stats["company_count_before"], 5)
        self.assertEqual(stats["company_count_after"], 3)
        self.assertEqual(stats["attachment_removed"], 2)
        self.assertEqual(stats["listed_domestic_count"], 1)
        self.assertEqual(stats["listed_abroad_only_count"], 2)
        self.assertEqual(stats["removed_flag_counts"], {"is_listed=false": 1, "is_listed=null": 1})

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
        payload = {**SAMPLE_PAYLOAD, "attachments": [attachment("sw_listed", "n1")]}
        filtered, _ = filter_listed_attachments(payload)
        self.assertEqual([item["company_id"] for item in filtered["companies"]], ["sw_listed"])


class ExportCompanyCsvListedOnlyTest(unittest.TestCase):
    def _rows(self, listed_only: bool) -> list[str]:
        with TemporaryDirectory() as directory:
            result = export_company_attachment_csv(
                SAMPLE_GRAPH, SAMPLE_PAYLOAD, "test", Path(directory), listed_only=listed_only
            )
            content = Path(result["company_node_csv"]).read_text(encoding="utf-8-sig")
        return [line for line in content.splitlines()[1:] if line.strip()]

    def test_defaults_to_listed_only(self):
        self.assertEqual(len(self._rows(listed_only=True)), 3)

    def test_include_unlisted_exports_everything(self):
        self.assertEqual(len(self._rows(listed_only=False)), 5)


if __name__ == "__main__":
    unittest.main()
