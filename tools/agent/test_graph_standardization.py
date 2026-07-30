from __future__ import annotations

import unittest

from tools.agent.common import standardize_graph


class SingletonContainsLeafTests(unittest.TestCase):
    def test_removes_only_singleton_leaf_below_non_root_parent(self) -> None:
        graph = standardize_graph(
            {
                "industry": "测试行业",
                "nodes": [
                    {"id": "root", "name": "测试行业", "level": 0, "chain_position": "root"},
                    {"id": "category", "name": "饮料", "level": 2, "parent_id": "root"},
                    {"id": "nfc", "name": "NFC果汁", "level": 3, "parent_id": "category"},
                ],
                "edges": [
                    {"source": "root", "target": "category", "relation_type": "contains"},
                    {"source": "category", "target": "nfc", "relation_type": "contains"},
                ],
            },
            "test",
        )

        self.assertEqual({node["id"] for node in graph["nodes"]}, {"root", "category"})
        self.assertEqual(
            [(edge["source"], edge["target"]) for edge in graph["edges"]],
            [("root", "category")],
        )

    def test_preserves_root_only_child_and_non_leaf_subtree(self) -> None:
        graph = standardize_graph(
            {
                "industry": "测试行业",
                "nodes": [
                    {"id": "root", "name": "测试行业", "level": 0, "chain_position": "root"},
                    {"id": "branch", "name": "一级环节", "level": 1, "parent_id": "root"},
                    {"id": "category", "name": "产品类别", "level": 2, "parent_id": "branch"},
                    {"id": "product", "name": "具体产品", "level": 3, "parent_id": "category"},
                    {"id": "product_2", "name": "另一产品", "level": 3, "parent_id": "category"},
                ],
                "edges": [
                    {"source": "root", "target": "branch", "relation_type": "contains"},
                    {"source": "branch", "target": "category", "relation_type": "contains"},
                    {"source": "category", "target": "product", "relation_type": "contains"},
                    {"source": "category", "target": "product_2", "relation_type": "contains"},
                ],
            },
            "test",
        )

        self.assertEqual({node["id"] for node in graph["nodes"]}, {"root", "branch", "category", "product", "product_2"})


if __name__ == "__main__":
    unittest.main()
