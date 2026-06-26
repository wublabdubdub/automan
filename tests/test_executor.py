from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automan_core.executor import _preflight_benchmarksql, _prepare_benchmark_run_dir
from automan_core.models import ConnectionInfo, DatabaseProfile, RunSpec, Target


class ExecutorPreparationTest(unittest.TestCase):
    def test_preflight_requires_linux_built_benchmarksql_dist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools/benchmarksql").mkdir(parents=True)

            result = _preflight_benchmarksql(root)

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("build-benchmarksql", result.stderr)

    def test_prepare_run_dir_renders_mars3_options_without_touching_global_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_tool = root / "tools/benchmarksql"
            global_run = global_tool / "run"
            (global_run / "sql.common").mkdir(parents=True)
            (global_run / "sql.postgres").mkdir(parents=True)
            (global_run / "runBenchmark.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (global_tool / "lib").mkdir()
            (global_tool / "dist").mkdir()
            global_table_creates = global_run / "sql.common/tableCreates.sql"
            global_table_creates.write_text("global default\n", encoding="utf-8")

            ddl_dir = root / "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only"
            (ddl_dir / "sql.common").mkdir(parents=True)
            (ddl_dir / "sql.postgres").mkdir(parents=True)
            (ddl_dir / "sql.common/tableCreates.sql").write_text(
                """create table bmsql_stock (s_w_id integer)
USING MARS3
WITH(
  mars3options='prefer_load_mode=single,rowstore_size=64',
  compresstype=zstd,
  compresslevel=1,
  compress_threshold=1200
)
DISTRIBUTED MASTERONLY;
""",
                encoding="utf-8",
            )

            profile = DatabaseProfile(
                id="ymatrix_mars3_master_only",
                display_name="YMatrix mars3 master only",
                database_type="ymatrix",
                storage_engine="mars3",
                test_mode="master_only",
                ddl_profile="ymatrix_mars3_master_only",
                ddl_dir="benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only",
                requires_ddl_confirmation=True,
            )
            conn = ConnectionInfo(
                ssh_host="db-host",
                ssh_port=22,
                ssh_user="root",
                ssh_password="secret",
                remote_workdir="/root/automan",
                db_host="db-host",
                db_port=5432,
                db_name="postgres",
                db_user="postgres",
                db_password="secret",
            )
            target = Target(
                profile=profile,
                connection=conn,
                recommended_params={},
                accepted_params={},
                apply_params=False,
                host_facts={},
                mars3_options={
                    "prefer_load_mode": "bulk",
                    "rowstore_size": 128,
                    "compresstype": "zstd",
                    "compresslevel": 3,
                    "compress_threshold": 2048,
                },
            )
            run = RunSpec(
                run_id="run1",
                target_id=profile.id,
                warehouse=100,
                terminals=100,
                load_workers=8,
                run_mins=60,
                ddl_profile=profile.ddl_profile,
                ddl_dir=profile.ddl_dir,
                properties_path=root / "work/run1/tpcc.properties",
                work_dir=root / "work/run1",
                benchmark_run_dir=root / "work/run1/benchmarksql/run",
            )

            _prepare_benchmark_run_dir(root, target, run)

            rendered = (run.benchmark_run_dir / "sql.common/tableCreates.sql").read_text(encoding="utf-8")
            self.assertIn("prefer_load_mode=bulk,rowstore_size=128", rendered)
            self.assertIn("compresslevel=3", rendered)
            self.assertIn("compress_threshold=2048", rendered)
            self.assertEqual(global_table_creates.read_text(encoding="utf-8"), "global default\n")
            self.assertTrue((root / "work/run1/benchmarksql/lib").exists())
            self.assertTrue((root / "work/run1/benchmarksql/dist").exists())


if __name__ == "__main__":
    unittest.main()
