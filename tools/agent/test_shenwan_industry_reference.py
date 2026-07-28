from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.agent_service import _build_candidate_command
from backend.app.schemas import AgentRunRequest
from tools.agent.search.shenwan_industry_reference import (
    SHENWAN_FILTER_MODEL,
    build_indunamesw_rows,
    call_bailian_indunamesw_filter,
    normalize_indunamesw,
    refresh_indunamesw_table,
    validate_indunamesw_selection,
)
from tools.agent.search.staged_bailian_builder import (
    blueprint_chain_position_coverage,
    build_seed_blueprint_prompt,
    build_seed_prompt,
    call_bailian_seed_blueprint,
    call_bailian_seed_graph,
    graph_chain_position_coverage,
)


class ShenwanIndustryReferenceTests(unittest.TestCase):
    def test_normalize_removes_sw_level_suffix(self) -> None:
        self.assertEqual(normalize_indunamesw("白酒Ⅰ"), "白酒")
        self.assertEqual(normalize_indunamesw("白酒Ⅱ"), "白酒")
        self.assertEqual(normalize_indunamesw("白酒Ⅲ"), "白酒")
        self.assertEqual(normalize_indunamesw("LEDⅢ"), "LED")
        self.assertEqual(normalize_indunamesw("综合 III"), "综合")
        self.assertEqual(normalize_indunamesw("维生素B"), "维生素B")

    def test_build_table_keeps_unique_tree_paths_and_collapses_same_name_levels(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.csv"
            output_path = Path(directory) / "indunamesw.csv"
            with source_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=("indunamesw", "indunamesw1", "indunamesw2", "indunamesw3"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "indunamesw": "白酒Ⅲ",
                        "indunamesw1": "食品饮料",
                        "indunamesw2": "白酒Ⅱ",
                        "indunamesw3": "白酒Ⅲ",
                    }
                )
                writer.writerow(
                    {
                        "indunamesw": "白酒Ⅲ",
                        "indunamesw1": "食品饮料",
                        "indunamesw2": "白酒Ⅱ",
                        "indunamesw3": "白酒Ⅲ",
                    }
                )
                writer.writerow(
                    {
                        "indunamesw": "调味发酵品Ⅲ",
                        "indunamesw1": "食品饮料",
                        "indunamesw2": "调味发酵品Ⅱ",
                        "indunamesw3": "调味发酵品Ⅲ",
                    }
                )

            rows = build_indunamesw_rows(source_path)
            row_by_path = {row["indunamesw_path"]: row for row in rows}
            self.assertEqual(set(row_by_path), {"食品饮料 > 白酒", "食品饮料 > 调味发酵品"})
            white_liquor = row_by_path["食品饮料 > 白酒"]
            self.assertEqual(white_liquor["level"], 2)
            self.assertEqual(white_liquor["indunamesw1"], "食品饮料")
            self.assertEqual(white_liquor["indunamesw2"], "白酒")
            self.assertEqual(white_liquor["indunamesw3"], "")
            self.assertEqual(white_liquor["occurrence_count"], 2)
            self.assertEqual(white_liquor["raw_leaf_names"], "白酒Ⅲ")
            self.assertEqual(white_liquor["raw_paths"], "食品饮料 > 白酒Ⅱ > 白酒Ⅲ")

            refresh_indunamesw_table(source_path, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                written = list(csv.DictReader(file))
            self.assertEqual(
                [row["indunamesw_path"] for row in written],
                ["食品饮料 > 白酒", "食品饮料 > 调味发酵品"],
            )

    def test_selection_only_accepts_complete_paths_from_tree(self) -> None:
        rows = [
            {
                "indunamesw": "白酒",
                "level": 2,
                "indunamesw_path": "食品饮料 > 白酒",
            },
            {
                "indunamesw": "乳品",
                "level": 2,
                "indunamesw_path": "食品饮料 > 乳品",
            },
        ]
        selection = validate_indunamesw_selection(
            {
                "industry": "食品饮料行业",
                "selected_categories": [
                    {
                        "path": "食品饮料 > 白酒",
                        "name": "模型可以写错，校验后以表为准",
                        "reason": "核心品类",
                        "suggested_role": "core",
                    },
                    {
                        "path": "食品饮料 > 模型虚构分类",
                        "reason": "无效",
                        "suggested_role": "core",
                    },
                ],
            },
            rows,
        )
        self.assertEqual(
            selection["selected_categories"],
            [
                {
                    "path": "食品饮料 > 白酒",
                    "name": "白酒",
                    "level": 2,
                    "reason": "核心品类",
                    "suggested_role": "core",
                }
            ],
        )
        self.assertEqual(selection["role_coverage"]["core"], 1)

    def test_selection_required_roles_accepts_cross_root_downstream_paths(self) -> None:
        rows = [
            {"indunamesw": "白酒", "level": 2, "indunamesw_path": "食品饮料 > 白酒"},
            {"indunamesw": "种植业", "level": 2, "indunamesw_path": "农林牧渔 > 种植业"},
            {"indunamesw": "餐饮", "level": 3, "indunamesw_path": "社会服务 > 酒店餐饮 > 餐饮"},
        ]
        selection = validate_indunamesw_selection(
            {
                "industry": "食品饮料行业",
                "selected_categories": [
                    {"path": rows[0]["indunamesw_path"], "suggested_role": "core"},
                    {"path": rows[1]["indunamesw_path"], "suggested_role": "upstream"},
                    {"path": rows[2]["indunamesw_path"], "suggested_role": "downstream"},
                ],
            },
            rows,
            required_roles=("core", "upstream", "downstream"),
        )
        self.assertEqual(selection["role_coverage"]["downstream"], 1)
        self.assertEqual(selection["selected_categories"][2]["path"], "社会服务 > 酒店餐饮 > 餐饮")

    def test_filter_call_forces_plus_without_thinking_search_or_tools(self) -> None:
        response = SimpleNamespace(
            output_text=(
                '{"industry":"食品饮料行业","selected_categories":['
                '{"path":"食品饮料 > 白酒","name":"白酒",'
                '"reason":"核心品类","suggested_role":"core"},'
                '{"path":"农林牧渔 > 种植业","name":"种植业",'
                '"reason":"原料供给","suggested_role":"upstream"},'
                '{"path":"社会服务 > 酒店餐饮 > 餐饮","name":"餐饮",'
                '"reason":"终端消费","suggested_role":"downstream"}]}'
            )
        )
        rows = [
            {"indunamesw": "白酒", "level": 2, "indunamesw_path": "食品饮料 > 白酒"},
            {"indunamesw": "种植业", "level": 2, "indunamesw_path": "农林牧渔 > 种植业"},
            {"indunamesw": "餐饮", "level": 3, "indunamesw_path": "社会服务 > 酒店餐饮 > 餐饮"},
        ]
        with patch(
            "tools.agent.search.shenwan_industry_reference.call_bailian_responses",
            return_value=response,
        ) as mocked_call:
            selection, _, prompt = call_bailian_indunamesw_filter("食品饮料行业", rows)

        self.assertEqual(selection["selected_categories"][0]["path"], "食品饮料 > 白酒")
        self.assertIn("一级 > 二级 > 三级", prompt)
        self.assertIn("扫描整张分类树", prompt)
        self.assertEqual(mocked_call.call_count, 1)
        kwargs = mocked_call.call_args.kwargs
        self.assertEqual(kwargs["model"], SHENWAN_FILTER_MODEL)
        self.assertFalse(kwargs["use_search_tools"])
        self.assertFalse(kwargs["enable_thinking"])

    def test_filter_missing_downstream_retries_with_cross_root_correction(self) -> None:
        first_response = SimpleNamespace(
            output_text=(
                '{"industry":"食品饮料行业","selected_categories":['
                '{"path":"食品饮料 > 白酒","suggested_role":"core"},'
                '{"path":"农林牧渔 > 种植业","suggested_role":"upstream"}]}'
            )
        )
        corrected_response = SimpleNamespace(
            output_text=(
                '{"industry":"食品饮料行业","selected_categories":['
                '{"path":"食品饮料 > 白酒","suggested_role":"core"},'
                '{"path":"农林牧渔 > 种植业","suggested_role":"upstream"},'
                '{"path":"商贸零售 > 一般零售 > 超市","suggested_role":"downstream"}]}'
            )
        )
        rows = [
            {"indunamesw": "白酒", "level": 2, "indunamesw_path": "食品饮料 > 白酒"},
            {"indunamesw": "种植业", "level": 2, "indunamesw_path": "农林牧渔 > 种植业"},
            {"indunamesw": "超市", "level": 3, "indunamesw_path": "商贸零售 > 一般零售 > 超市"},
        ]
        with patch(
            "tools.agent.search.shenwan_industry_reference.call_bailian_responses",
            side_effect=[first_response, corrected_response],
        ) as mocked_call:
            selection, raw_text, prompt = call_bailian_indunamesw_filter("食品饮料行业", rows)

        self.assertEqual(mocked_call.call_count, 2)
        self.assertEqual(selection["role_coverage"]["downstream"], 1)
        self.assertIn("其他申万一级根节点", prompt)
        self.assertEqual(raw_text, corrected_response.output_text)

    def test_blueprint_missing_downstream_is_corrected_once(self) -> None:
        def blueprint(position: str) -> dict:
            return {
                "primary_classification_axis": "测试轴",
                "level_one_design": [
                    {"name": "原料", "chain_position": "upstream"},
                    {"name": "品类一", "chain_position": "midstream"},
                    {"name": "品类二", "chain_position": "midstream"},
                    {"name": "品类三", "chain_position": "midstream"},
                    {"name": "终端", "chain_position": position},
                ],
            }

        with patch(
            "tools.agent.search.staged_bailian_builder._call_json_prompt",
            side_effect=[(blueprint("midstream"), "first"), (blueprint("downstream"), "corrected")],
        ) as mocked_call:
            result, raw_text, prompt = call_bailian_seed_blueprint("食品饮料行业")

        self.assertEqual(mocked_call.call_count, 2)
        self.assertEqual(raw_text, "corrected")
        self.assertEqual(blueprint_chain_position_coverage(result)["downstream"], 1)
        self.assertIn("产业链位置覆盖硬约束", prompt)

    def test_seed_graph_missing_downstream_is_corrected_once(self) -> None:
        def graph(position: str) -> dict:
            return {
                "nodes": [
                    {"id": "root", "level": 0, "chain_position": "root"},
                    {"id": "up", "level": 1, "chain_position": "upstream"},
                    {"id": "core1", "level": 1, "chain_position": "midstream"},
                    {"id": "core2", "level": 1, "chain_position": "midstream"},
                    {"id": "core3", "level": 1, "chain_position": "midstream"},
                    {"id": "terminal", "level": 1, "chain_position": position},
                ],
                "edges": [],
            }

        with patch(
            "tools.agent.search.staged_bailian_builder._call_json_prompt",
            side_effect=[(graph("midstream"), "first"), (graph("downstream"), "corrected")],
        ) as mocked_call:
            result, raw_text, prompt = call_bailian_seed_graph(
                "food_beverage",
                "食品饮料行业",
                "L0-L4",
            )

        self.assertEqual(mocked_call.call_count, 2)
        self.assertEqual(raw_text, "corrected")
        self.assertEqual(graph_chain_position_coverage(result)["downstream"], 1)
        self.assertIn("一级骨架图谱", prompt)

    def test_reference_is_advisory_in_blueprint_and_graph_prompts(self) -> None:
        reference = {
            "selected_categories": [
                {"path": "食品饮料 > 白酒", "name": "白酒", "reason": "核心品类"}
            ]
        }
        blueprint_prompt = build_seed_blueprint_prompt("食品饮料行业", reference)
        graph_prompt = build_seed_prompt("food_beverage", "食品饮料行业", "L0-L4", {}, reference)
        for prompt in (blueprint_prompt, graph_prompt):
            self.assertIn("食品饮料 > 白酒", prompt)
            self.assertIn("不是最终交付", prompt)
            self.assertIn("重命名", prompt)

    def test_prompts_do_not_mention_shenwan_when_reference_is_disabled(self) -> None:
        blueprint_prompt = build_seed_blueprint_prompt("食品饮料行业")
        graph_prompt = build_seed_prompt("food_beverage", "食品饮料行业", "L0-L4", {})
        self.assertNotIn("申万", blueprint_prompt)
        self.assertNotIn("申万", graph_prompt)

    def test_backend_only_adds_cli_flag_when_reference_is_enabled(self) -> None:
        default_command = _build_candidate_command(
            "food_beverage",
            "食品饮料行业",
            "L0-L4",
            "skeleton",
        )
        enabled_command = _build_candidate_command(
            "food_beverage",
            "食品饮料行业",
            "L0-L4",
            "skeleton",
            use_shenwan_reference=True,
        )
        self.assertNotIn("--use-shenwan-reference", default_command)
        self.assertIn("--use-shenwan-reference", enabled_command)

    def test_api_request_defaults_reference_to_disabled(self) -> None:
        request = AgentRunRequest(industry_id="food_beverage")
        self.assertFalse(request.use_shenwan_reference)


if __name__ == "__main__":
    unittest.main()
