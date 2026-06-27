from __future__ import annotations

import unittest
from pathlib import Path


class Mars3DDLTest(unittest.TestCase):
    def test_mars3_table_creates_use_confirmed_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl = root / "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only/sql.common/tableCreates.sql"
        text = ddl.read_text(encoding="utf-8")

        self.assertLess(text.lower().find("create extension if not exists matrixts"), text.lower().find("create table"))
        self.assertEqual(text.count("USING MARS3"), 10)
        self.assertEqual(text.count("DISTRIBUTED MASTERONLY"), 10)
        self.assertIn("mars3options='prefer_load_mode=single,rowstore_size=64'", text)
        self.assertIn("compresstype=zstd", text)
        self.assertIn("compresslevel=1", text)
        self.assertIn("compress_threshold=1200", text)

    def test_tpcc_drop_sql_is_idempotent_for_all_profiles(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl_root = root / "benchmarks/tpcc/benchmarksql/ddl"
        for profile in ("postgresql_heap_single_node", "ymatrix_heap_master_only", "ymatrix_mars3_master_only"):
            with self.subTest(profile=profile):
                text = (ddl_root / profile / "sql.common/tableDrops.sql").read_text(encoding="utf-8").lower()
                self.assertNotIn("drop table bmsql_", text)
                self.assertNotIn("drop sequence bmsql_", text)
                self.assertEqual(text.count("drop table if exists"), 10)
                self.assertEqual(text.count("drop sequence if exists"), 1)


if __name__ == "__main__":
    unittest.main()
