from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.agent import build_l2_flow_relations as build_command
from tools.agent.common import write_json
from tools.agent.l2_flow_relations import (
    apply_l1_l2_projection,
    build_candidate_pairs,
    build_l1_l2_projected_edges,
    build_payload,
    call_pair_batch,
    compact_l2_catalog,
    decision_cache_key,
    pair_id,
    parse_pair_verdicts,
)
from tools.agent.validators.l2_flow_relation_validator import validate_l2_flow_relations


def sample_graph() -> dict:
    nodes = [
        {
            "id": "root",
            "name": "测试行业",
            "level": 0,
            "parent_id": "",
            "chain_position": "root",
            "source_urls": ["https://example.com/root"],
            "evidence_ids": ["ev_root"],
        },
        {
            "id": "l1_raw",
            "name": "原料",
            "level": 1,
            "parent_id": "root",
            "chain_position": "upstream",
            "source_urls": ["https://example.com/raw"],
            "evidence_ids": ["ev_raw"],
        },
        {
            "id": "l1_product",
            "name": "产品",
            "level": 1,
            "parent_id": "root",
            "chain_position": "downstream",
            "source_urls": ["https://example.com/product"],
            "evidence_ids": ["ev_product"],
        },
        {
            "id": "grain",
            "name": "粮食作物",
            "level": 2,
            "parent_id": "l1_raw",
            "chain_position": "upstream",
            "description": "白酒生产所需的主要粮食原料。",
            "source_urls": ["https://example.com/grain"],
            "evidence_ids": ["ev_grain"],
        },
        {
            "id": "sugar",
            "name": "糖料作物",
            "level": 2,
            "parent_id": "l1_raw",
            "chain_position": "upstream",
            "description": "含糖食品的上游原料。",
            "source_urls": ["https://example.com/sugar"],
            "evidence_ids": ["ev_sugar"],
        },
        {
            "id": "liquor",
            "name": "白酒",
            "level": 2,
            "parent_id": "l1_product",
            "chain_position": "downstream",
            "description": "以粮食作物为主要原料酿造。",
            "source_urls": ["https://example.com/liquor"],
            "evidence_ids": ["ev_liquor"],
        },
        {
            "id": "candy",
            "name": "糖果",
            "level": 2,
            "parent_id": "l1_product",
            "chain_position": "downstream",
            "description": "使用糖类原料生产的食品。",
            "source_urls": ["https://example.com/candy"],
            "evidence_ids": ["ev_candy"],
        },
    ]
    return {
        "industry": "测试行业",
        "schema_version": "standard_industry_graph_v0.2_agent",
        "nodes": nodes,
        "edges": [
            {"source": "root", "target": "l1_raw", "relation_type": "contains"},
            {"source": "root", "target": "l1_product", "relation_type": "contains"},
            {"source": "l1_raw", "target": "grain", "relation_type": "contains"},
            {"source": "l1_raw", "target": "sugar", "relation_type": "contains"},
            {"source": "l1_product", "target": "liquor", "relation_type": "contains"},
            {"source": "l1_product", "target": "candy", "relation_type": "contains"},
        ],
    }


class L2FlowPairwiseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = sample_graph()
        self.catalog = compact_l2_catalog(self.graph)
        self.catalog_by_id = {node["id"]: node for node in self.catalog}

    def test_candidate_recall_only_uses_cross_branch_pairs(self) -> None:
        pairs, summary = build_candidate_pairs(
            self.graph,
            self.catalog,
            candidates_per_node=1,
            negative_audit_rate=0,
        )
        self.assertGreater(len(pairs), 0)
        self.assertEqual(summary["cross_branch_pair_count"], 4)
        for pair in pairs:
            left = self.catalog_by_id[pair["node_a_id"]]
            right = self.catalog_by_id[pair["node_b_id"]]
            self.assertNotEqual(left["branch_id"], right["branch_id"])
        self.assertIn(pair_id("grain", "liquor"), {pair["pair_id"] for pair in pairs})

    def test_parser_is_strict_and_rejects_duplicate_pair_lines(self) -> None:
        first = pair_id("grain", "liquor")
        second = pair_id("sugar", "candy")
        parsed = parse_pair_verdicts(
            f"{first}:A_TO_B\n{second}:NO\n解释：忽略这一行",
            {first, second},
        )
        self.assertEqual(parsed, {first: "A_TO_B", second: "NO"})
        duplicate = parse_pair_verdicts(f"{first}:NO\n{first}:A_TO_B", {first})
        self.assertEqual(duplicate, {})

    def test_pair_call_disables_tools_and_uses_low_temperature(self) -> None:
        identifier = pair_id("grain", "liquor")
        pairs = [{"pair_id": identifier, "node_a_id": "grain", "node_b_id": "liquor"}]

        class Response:
            output_text = f"{identifier}:A_TO_B"

        with patch("tools.agent.l2_flow_relations.call_bailian_responses", return_value=Response()) as mocked:
            verdicts, _, _ = call_pair_batch(self.catalog_by_id, pairs)
        self.assertEqual(verdicts, {identifier: "A_TO_B"})
        kwargs = mocked.call_args.kwargs
        self.assertFalse(kwargs["use_search_tools"])
        self.assertFalse(kwargs["enable_thinking"])
        self.assertFalse(kwargs["include_web_extractor"])
        self.assertLessEqual(kwargs["temperature"], 0.3)
        self.assertGreater(kwargs["max_output_tokens"], 0)

    def test_cache_key_changes_when_either_node_content_changes(self) -> None:
        pair = {"node_a_id": "grain", "node_b_id": "liquor"}
        first = decision_cache_key(pair, self.catalog_by_id)
        changed = {key: dict(value) for key, value in self.catalog_by_id.items()}
        changed["grain"]["node_content_hash"] = "changed"
        self.assertNotEqual(first, decision_cache_key(pair, changed))

    def test_fixed_script_materializes_only_positive_pair_decisions(self) -> None:
        pairs = [
            {
                "pair_id": pair_id("grain", "liquor"),
                "node_a_id": "grain",
                "node_b_id": "liquor",
                "score": 1.0,
                "selection_reasons": ["grain:global_similarity"],
            },
            {
                "pair_id": pair_id("candy", "sugar"),
                "node_a_id": "candy",
                "node_b_id": "sugar",
                "score": 1.0,
                "selection_reasons": ["candy:global_similarity"],
            },
        ]
        decisions = [
            {"pair_id": pairs[0]["pair_id"], "verdict": "A_TO_B", "decision_source": "model_batch"},
            {"pair_id": pairs[1]["pair_id"], "verdict": "NO", "decision_source": "cache"},
        ]
        summary = {
            "cross_branch_pair_count": 4,
            "shortlisted_pair_count": 2,
            "negative_audit_pair_count": 0,
            "candidate_pair_count": 2,
            "candidates_per_node": 1,
            "negative_audit_rate": 0,
        }
        payload = apply_l1_l2_projection(
            build_payload("test", self.graph, self.catalog, pairs, decisions, summary),
            self.graph,
        )
        self.assertEqual(len(payload["edges"]), 1)
        self.assertEqual(payload["edges"][0]["source"], "grain")
        self.assertEqual(payload["edges"][0]["target"], "liquor")
        self.assertEqual(len(payload["projected_edges"]), 2)
        projected_pairs = {(edge["source"], edge["target"]) for edge in payload["projected_edges"]}
        self.assertEqual(projected_pairs, {("l1_raw", "liquor"), ("grain", "l1_product")})
        self.assertNotIn(("l1_raw", "l1_product"), projected_pairs)
        report = validate_l2_flow_relations(payload, self.graph, "test")
        self.assertEqual(report["error_count"], 0, report["issues"])

    def test_l1_l2_postprocess_creates_cross_projection_without_l1_l1_edge(self) -> None:
        l2_edges = [
            {
                "id": "grain__upstream_downstream__liquor",
                "source": "grain",
                "target": "liquor",
                "relation_type": "upstream_downstream",
                "relation_layer": "l2_flow",
                "relation_weight": 1.0,
                "source_urls": ["https://example.com/grain"],
                "evidence_ids": ["ev_grain"],
                "confidence": 0.8,
            },
            {
                "id": "grain__upstream_downstream__candy",
                "source": "grain",
                "target": "candy",
                "relation_type": "upstream_downstream",
                "relation_layer": "l2_flow",
                "relation_weight": 1.0,
                "source_urls": ["https://example.com/candy"],
                "evidence_ids": ["ev_candy"],
                "confidence": 0.8,
            },
        ]
        projected = build_l1_l2_projected_edges(self.graph, l2_edges)
        self.assertEqual(len(projected), 3)
        projected_by_pair = {(edge["source"], edge["target"]): edge for edge in projected}
        self.assertNotIn(("l1_raw", "l1_product"), projected_by_pair)
        self.assertIn(("l1_raw", "liquor"), projected_by_pair)
        self.assertIn(("l1_raw", "candy"), projected_by_pair)
        collapsed = projected_by_pair[("grain", "l1_product")]
        self.assertEqual(collapsed["projected_from_count"], 2)
        self.assertEqual(collapsed["relation_weight"], 1.0)
        self.assertEqual(collapsed["confidence"], 0.8)

    def test_second_build_reuses_pair_decision_cache_without_model_calls(self) -> None:
        def all_no(catalog_by_id: dict, pairs: list[dict]) -> tuple[dict[str, str], str, str]:
            verdicts = {pair["pair_id"]: "NO" for pair in pairs}
            raw = "\n".join(f"{identifier}:NO" for identifier in verdicts)
            return verdicts, "test prompt", raw

        with TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            write_json(output_dir / "graph.json", self.graph)
            with (
                patch.object(build_command, "industry_dir", return_value=output_dir),
                patch.object(build_command, "load_graph", return_value=self.graph),
                patch.object(build_command, "call_pair_batch", side_effect=all_no) as first_call,
            ):
                build_command.run_l2_flow_relation_build("test")
            self.assertGreater(first_call.call_count, 0)
            self.assertTrue((output_dir / "l2_flow_pair_decisions.jsonl").exists())

            with (
                patch.object(build_command, "industry_dir", return_value=output_dir),
                patch.object(build_command, "load_graph", return_value=self.graph),
                patch.object(build_command, "call_pair_batch", side_effect=AssertionError("cache miss")) as second_call,
            ):
                build_command.run_l2_flow_relation_build("test")
            second_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
