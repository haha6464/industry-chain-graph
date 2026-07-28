from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.agent.bailian_client import DEFAULT_SEARCH_STRATEGY, MIN_OPENAI_VERSION, _version_tuple, bailian_tools


class BailianClientTests(unittest.TestCase):
    def test_default_search_strategy_is_agent(self) -> None:
        self.assertEqual(DEFAULT_SEARCH_STRATEGY, "agent")

    def test_openai_version_floor_supports_bailian_web_search(self) -> None:
        self.assertEqual(MIN_OPENAI_VERSION, (2, 28, 0))
        self.assertLess(_version_tuple("1.93.0"), MIN_OPENAI_VERSION)
        self.assertGreaterEqual(_version_tuple("2.28.0"), MIN_OPENAI_VERSION)

    def test_web_extractor_is_disabled_even_when_legacy_flag_is_true(self) -> None:
        with patch.dict("os.environ", {"BAILIAN_ENABLE_CODE_INTERPRETER": "false"}):
            tool_types = [tool["type"] for tool in bailian_tools(include_web_extractor=True)]

        self.assertEqual(tool_types, ["web_search"])


if __name__ == "__main__":
    unittest.main()
