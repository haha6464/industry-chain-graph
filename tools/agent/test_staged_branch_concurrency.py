from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tools.agent.search.staged_bailian_builder import staged_branch_concurrency


class StagedBranchConcurrencyTest(unittest.TestCase):
    def test_defaults_to_four_and_clamps_to_pending_branch_count(self) -> None:
        with patch.dict(os.environ, {"BAILIAN_STAGED_BRANCH_MAX_CONCURRENCY": "invalid"}):
            self.assertEqual(staged_branch_concurrency(7), 4)
            self.assertEqual(staged_branch_concurrency(2), 2)

    def test_zero_expands_all_pending_branches_in_parallel(self) -> None:
        with patch.dict(os.environ, {"BAILIAN_STAGED_BRANCH_MAX_CONCURRENCY": "0"}):
            self.assertEqual(staged_branch_concurrency(7), 7)

    def test_one_keeps_the_serial_fallback(self) -> None:
        with patch.dict(os.environ, {"BAILIAN_STAGED_BRANCH_MAX_CONCURRENCY": "1"}):
            self.assertEqual(staged_branch_concurrency(7), 1)

    def test_empty_worklist_still_returns_a_valid_executor_size(self) -> None:
        with patch.dict(os.environ, {"BAILIAN_STAGED_BRANCH_MAX_CONCURRENCY": "4"}):
            self.assertEqual(staged_branch_concurrency(0), 1)


if __name__ == "__main__":
    unittest.main()
