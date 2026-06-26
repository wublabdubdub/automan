from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import automan_core.executor as executor
from automan_core.config import load_yaml, write_json
from automan_core.executor import _execute_run, _preflight_benchmarksql, _prepare_benchmark_run_dir
from automan_core.models import ConnectionInfo, DatabaseProfile, RunSpec, Target
from automan_core.ssh import CommandResult


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

    def test_first_run_skips_destroy_when_schema_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            commands: list[str] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    commands.append(command[0])
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "campaign", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseBuild.sh", "./runBenchmark.sh"])
            status = load_yaml(root / "runs" / run.run_id / "status.json")
            self.assertEqual(status["status"], "success")

    def test_first_run_destroys_when_schema_already_has_tpcc_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            commands: list[str] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "11\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    commands.append(command[0])
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "campaign", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseDestroy.sh", "./runDatabaseBuild.sh", "./runBenchmark.sh"])

    def test_conflicting_params_on_same_config_host_fail_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self._profile()
            conn = self._connection()
            targets = [
                Target(profile, conn, {}, {"max_connections": "128"}, True, {}),
                Target(profile, conn, {}, {"max_connections": "256"}, True, {}),
            ]
            campaign_dir = root / "runs/campaigns/campaign"
            campaign_dir.mkdir(parents=True)
            write_json(
                campaign_dir / "progress.json",
                {
                    "campaign_id": "campaign",
                    "status": "planned",
                    "targets": [{"target_id": profile.id, "status": "pending", "current_run": None, "current_phase": None}],
                    "failed_runs": 0,
                },
            )
            write_json(campaign_dir / "status.json", {"campaign_id": "campaign", "status": "planned"})

            old_execution = executor._preflight_execution_host
            old_benchmark = executor._preflight_benchmarksql
            old_apply = executor.apply_database_params
            try:
                executor._preflight_execution_host = lambda targets: CommandResult("execution", 0, "", "")
                executor._preflight_benchmarksql = lambda root: CommandResult("benchmarksql", 0, "", "")
                executor.apply_database_params = lambda profile, connection, params: [CommandResult("apply", 0, "", "")]

                executor.execute_campaign(root, "campaign", targets, [])
            finally:
                executor._preflight_execution_host = old_execution
                executor._preflight_benchmarksql = old_benchmark
                executor.apply_database_params = old_apply

            status = load_yaml(campaign_dir / "status.json")
            self.assertEqual(status["status"], "failed")
            progress = load_yaml(campaign_dir / "progress.json")
            self.assertEqual(progress["targets"][0]["status"], "failed")
            self.assertEqual(progress["targets"][0]["current_phase"], "database_config_conflict")
            timeline = (campaign_dir / "timeline.jsonl").read_text(encoding="utf-8")
            self.assertIn("database_config_conflict", timeline)

    def test_database_config_failure_marks_target_error_before_runs_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self._profile()
            target = Target(profile, self._connection(), {}, {"max_connections": "128"}, True, {})
            campaign_dir = root / "runs/campaigns/campaign"
            campaign_dir.mkdir(parents=True)
            write_json(
                campaign_dir / "progress.json",
                {
                    "campaign_id": "campaign",
                    "status": "planned",
                    "targets": [{"target_id": profile.id, "status": "pending", "current_run": None, "current_phase": None}],
                    "failed_runs": 0,
                },
            )
            write_json(campaign_dir / "status.json", {"campaign_id": "campaign", "status": "planned"})

            old_execution = executor._preflight_execution_host
            old_benchmark = executor._preflight_benchmarksql
            old_apply = executor.apply_database_params
            try:
                executor._preflight_execution_host = lambda targets: CommandResult("execution", 0, "", "")
                executor._preflight_benchmarksql = lambda root: CommandResult("benchmarksql", 0, "", "")
                executor.apply_database_params = lambda profile, connection, params: [CommandResult("restart", 1, "", "sudo requires a password")]

                executor.execute_campaign(root, "campaign", [target], [])
            finally:
                executor._preflight_execution_host = old_execution
                executor._preflight_benchmarksql = old_benchmark
                executor.apply_database_params = old_apply

            progress = load_yaml(campaign_dir / "progress.json")
            self.assertEqual(progress["targets"][0]["status"], "failed")
            self.assertEqual(progress["targets"][0]["current_phase"], "database_config")
            self.assertIn("sudo requires a password", progress["targets"][0]["last_error"])

    def _run_fixture(self, root: Path, skip_destroy: bool) -> tuple[Path, Target, RunSpec]:
        profile = self._profile()
        target = Target(profile, self._connection(), {}, {}, False, {})
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
            skip_destroy=skip_destroy,
        )
        campaign_dir = root / "runs/campaigns/campaign"
        campaign_dir.mkdir(parents=True)
        write_json(
            campaign_dir / "progress.json",
            {
                "campaign_id": "campaign",
                "status": "running",
                "total_runs": 1,
                "finished_runs": 0,
                "success_runs": 0,
                "running_runs": 0,
                "failed_runs": 0,
                "pending_runs": 1,
                "targets": [
                    {
                        "target_id": profile.id,
                        "status": "pending",
                        "current_run": None,
                        "current_phase": None,
                        "finished_runs": 0,
                        "total_runs": 1,
                    }
                ],
            },
        )
        return root, target, run

    def _profile(self) -> DatabaseProfile:
        return DatabaseProfile(
            id="postgresql_heap_single_node",
            display_name="PostgreSQL heap single node",
            database_type="postgresql",
            storage_engine="heap",
            test_mode="single_node",
            ddl_profile="postgresql_heap_single_node",
            ddl_dir="benchmarks/tpcc/benchmarksql/ddl/postgresql_heap_single_node",
            requires_ddl_confirmation=False,
        )

    def _connection(self) -> ConnectionInfo:
        return ConnectionInfo(
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


if __name__ == "__main__":
    unittest.main()
