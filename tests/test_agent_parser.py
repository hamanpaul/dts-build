from __future__ import annotations

import unittest

from dtsbuild.agent_parser import _parse_agent_json


class AgentParserTest(unittest.TestCase):
    def test_parse_agent_json_handles_code_fence(self) -> None:
        parsed = _parse_agent_json(
            """```json
{"meta":{"project":"Demo"},"memory":{"memcfg_macro":""},"network":{"rows":[]},"gpio":{"rows":[]},"missing_fields":[],"assumptions":[]}
```"""
        )
        self.assertEqual(parsed["meta"]["project"], "Demo")
        self.assertEqual(parsed["meta"]["backend"], "agent")


if __name__ == "__main__":
    unittest.main()
