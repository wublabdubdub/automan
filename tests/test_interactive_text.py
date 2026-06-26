from __future__ import annotations

import unittest
from pathlib import Path


class InteractiveTextTest(unittest.TestCase):
    def test_prompts_are_valid_utf8_chinese_not_mojibake(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "automan_core/interactive.py").read_text(encoding="utf-8")

        self.assertIn("请选择数据库类型", text)
        self.assertIn("是否开始执行", text)
        self.assertIn("请输入 warehouses", text)
        self.assertNotIn("璇烽", text)
        self.assertNotIn("鎵ц", text)


if __name__ == "__main__":
    unittest.main()

