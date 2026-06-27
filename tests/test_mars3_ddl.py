from __future__ import annotations

import unittest
from pathlib import Path


class Mars3DDLTest(unittest.TestCase):
    def test_mars3_table_creates_use_confirmed_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl = root / "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only/sql.common/tableCreates.sql"
        text = ddl.read_text(encoding="utf-8")

        self.assertLess(text.lower().find("create extension if not exists matrixts"), text.lower().find("create table"))
        self.assertEqual(text.count("USING MARS3"), 2)
        self.assertEqual(text.count("DISTRIBUTED MASTERONLY"), 2)
        self.assertIn("mars3options='prefer_load_mode=single,rowstore_size=64'", text)
        self.assertIn("compresstype=zstd", text)
        self.assertIn("compresslevel=1", text)
        self.assertIn("compress_threshold=1200", text)

    def test_mars3_profile_keeps_mutable_tpcc_tables_on_heap(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl = root / "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only/sql.common/tableCreates.sql"
        text = ddl.read_text(encoding="utf-8")

        for table in ("bmsql_config", "bmsql_item"):
            start = text.find(f"create table {table}")
            end = text.find("create table", start + 1)
            segment = text[start:] if end < 0 else text[start:end]
            self.assertIn("USING MARS3", segment)

        mutable_tables = (
            "bmsql_warehouse",
            "bmsql_district",
            "bmsql_customer",
            "bmsql_history",
            "bmsql_new_order",
            "bmsql_oorder",
            "bmsql_order_line",
            "bmsql_stock",
        )
        for table in mutable_tables:
            with self.subTest(table=table):
                start = text.find(f"create table {table}")
                end = text.find("create table", start + 1)
                segment = text[start:] if end < 0 else text[start:end]
                self.assertNotIn("USING MARS3", segment)

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
                self.assertEqual(text.count(" cascade;"), 11)

    def test_tpcc_stored_procedure_drop_signatures_match_create_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ddl_root = root / "benchmarks/tpcc/benchmarksql/ddl"
        expected_lines = [
            "drop function if exists bmsql_proc_new_order(integer, integer, integer, integer[], integer[], integer[]);",
            "drop function if exists bmsql_proc_stock_level(integer, integer, integer);",
            "drop function if exists bmsql_proc_payment(integer, integer, integer, integer, integer, character varying, numeric);",
            "drop function if exists bmsql_proc_order_status(integer, integer, integer, character varying);",
            "drop function if exists bmsql_cid_from_clast(integer, integer, character varying);",
            "drop function if exists bmsql_proc_delivery_bg(integer, integer, timestamp without time zone);",
        ]
        for profile in ("postgresql_heap_single_node", "ymatrix_heap_master_only", "ymatrix_mars3_master_only"):
            with self.subTest(profile=profile):
                text = (ddl_root / profile / "sql.postgres/storedProcedureDrops.sql").read_text(encoding="utf-8").lower()
                self.assertNotIn("var(16)", text)
                self.assertNotIn("bmsql_proc_delivery_bg (integer, integer, integer)", text)
                for line in expected_lines:
                    self.assertIn(line, text)

    def test_benchmarksql_execjdbc_exits_nonzero_on_sql_errors(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "tools/benchmarksql/src/jdbc/ExecJDBC.java").read_text(encoding="utf-8")

        self.assertIn("private static boolean hadError = false;", text)
        self.assertIn("hadError = true;", text)
        self.assertIn("System.exit(1);", text)


if __name__ == "__main__":
    unittest.main()
