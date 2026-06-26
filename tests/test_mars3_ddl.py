from __future__ import annotations

import unittest
from pathlib import Path


class Mars3DDLTest(unittest.TestCase):
    def test_mars3_table_creates_use_confirmed_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl = root / "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only/sql.common/tableCreates.sql"
        text = ddl.read_text(encoding="utf-8")

        self.assertEqual(text.count("USING MARS3"), 10)
        self.assertEqual(text.count("DISTRIBUTED MASTERONLY"), 10)
        self.assertIn("mars3options='prefer_load_mode=single,rowstore_size=64'", text)
        self.assertIn("compresstype=zstd", text)
        self.assertIn("compresslevel=1", text)
        self.assertIn("compress_threshold=1200", text)


if __name__ == "__main__":
    unittest.main()

