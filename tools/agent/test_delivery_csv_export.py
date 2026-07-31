from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.agent.export_csv import (
    COMPANY_EDGE_FIELDS,
    COMPANY_NODE_FIELDS,
    INDUSTRY_NODE_FIELDS,
    INDUSTRYNODE_EDGE_FIELDS,
    INDUSTRYNODE_INDUSTRY_EDGE_FIELDS,
    INDUSTRYNODE_NODE_FIELDS,
    export_industry_csv,
)


class DeliveryCsvExportTest(unittest.TestCase):
    def test_food_beverage_exports_all_six_delivery_files(self) -> None:
        with TemporaryDirectory() as directory:
            result = export_industry_csv("food_beverage", Path(directory))
            expected = {
                "industry_node_csv": INDUSTRY_NODE_FIELDS,
                "industrynode_edge_csv": INDUSTRYNODE_EDGE_FIELDS,
                "industrynode_industry_edge_csv": INDUSTRYNODE_INDUSTRY_EDGE_FIELDS,
                "industrynode_node_csv": INDUSTRYNODE_NODE_FIELDS,
                "company_node_csv": COMPANY_NODE_FIELDS,
                "company_edge_csv": COMPANY_EDGE_FIELDS,
            }
            for key, header in expected.items():
                with Path(result[key]).open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.reader(file))
                self.assertEqual(rows[0], header)
                self.assertGreater(len(rows), 1)

            with Path(result["industry_node_csv"]).open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(next(csv.DictReader(file)), {"code": "FOOD", "name": "食品饮料", "ind_id": "041800"})
            with Path(result["industrynode_node_csv"]).open(encoding="utf-8-sig", newline="") as file:
                node_rows = list(csv.DictReader(file))
            self.assertTrue(all(row["节点id"].startswith("FOOD") for row in node_rows))
            self.assertTrue(all(row["节点行业code"] == "FOOD" for row in node_rows))


if __name__ == "__main__":
    unittest.main()
