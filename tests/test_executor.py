from __future__ import annotations

import subprocess
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

    def test_run_local_timeout_decodes_partial_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_run = executor.subprocess.run
            try:
                def fake_run(*args, **kwargs):
                    raise subprocess.TimeoutExpired(
                        cmd=["./runDatabaseBuild.sh"],
                        timeout=10800,
                        output=b"partial stdout\n",
                        stderr=b"partial stderr\n",
                    )

                executor.subprocess.run = fake_run

                result = executor._run_local(["./runDatabaseBuild.sh"], Path(tmp), timeout=10800)
            finally:
                executor.subprocess.run = old_run

            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.stdout, "partial stdout\n")
            self.assertEqual(result.stderr, "partial stderr\n")

    def test_host_queue_exception_records_job_last_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            old_execution = executor._preflight_execution_host
            old_benchmark = executor._preflight_benchmarksql
            old_host_queue = executor._execute_host_queue
            try:
                executor._preflight_execution_host = lambda targets: CommandResult("execution", 0, "", "")
                executor._preflight_benchmarksql = lambda root: CommandResult("benchmarksql", 0, "", "")

                def fail_host_queue(*args, **kwargs):
                    raise RuntimeError("data must be str, not bytes")

                executor._execute_host_queue = fail_host_queue

                executor.execute_job(root, "Job", [target], [run])
            finally:
                executor._preflight_execution_host = old_execution
                executor._preflight_benchmarksql = old_benchmark
                executor._execute_host_queue = old_host_queue

            job_dir = root / "runs/jobs/Job"
            status = load_yaml(job_dir / "status.json")
            progress = load_yaml(job_dir / "job.json")
            self.assertEqual(status["status"], "failed")
            self.assertIn("data must be str", status["last_error"])
            self.assertIn("data must be str", progress["last_error"])

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
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseBuild.sh", "./runBenchmark.sh"])
            status = load_yaml(root / "runs" / run.run_id / "status.json")
            self.assertEqual(status["status"], "success")
            result = load_yaml(root / "runs" / run.run_id / "logs" / "runBenchmark.sh.result.json")
            self.assertEqual(result["phase"], "runBenchmark.sh")
            self.assertEqual(result["exit_code"], 0)
            self.assertTrue((root / "runs" / run.run_id / "logs" / "command-results.jsonl").exists())

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
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseDestroy.sh", "./runDatabaseBuild.sh", "./runBenchmark.sh"])

    def test_stage_destroy_runs_only_destroy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            commands: list[str] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None

                def fake_run(command, cwd, timeout, env=None):
                    commands.append(command[0])
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run, stage="destroy")
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseDestroy.sh"])
            status = load_yaml(root / "runs" / run.run_id / "status.json")
            self.assertEqual(status["status"], "success")

    def test_stage_load_runs_only_build_without_destroy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=False)
            commands: list[str] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None

                def fake_run(command, cwd, timeout, env=None):
                    commands.append(command[0])
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run, stage="load")
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runDatabaseBuild.sh"])

    def test_stage_bench_runs_only_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=False)
            commands: list[str] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None

                def fake_run(command, cwd, timeout, env=None):
                    commands.append(command[0])
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run, stage="bench")
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._run_local = old_run_local

            self.assertEqual(commands, ["./runBenchmark.sh"])

    def test_benchmark_parent_dir_exists_before_run_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    if command[0] == "./runBenchmark.sh":
                        self.assertTrue((root / "runs" / run.run_id / "benchmark").is_dir())
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

    def test_zero_exit_with_fatal_benchmarksql_output_fails_and_records_last_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    if command[0] == "./runBenchmark.sh":
                        return CommandResult(
                            " ".join(command),
                            0,
                            "processing...\nERROR: password authentication failed for user postgres\n",
                            "",
                        )
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            status = load_yaml(root / "runs" / run.run_id / "status.json")
            progress = load_yaml(root / "runs/jobs/Job/job.json")
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["phase"], "runBenchmark.sh")
            self.assertIn("password authentication failed", status["last_error"])
            self.assertIn("password authentication failed", progress["last_error"])
            self.assertIn("password authentication failed", progress["targets"][0]["last_error"])

    def test_zero_exit_with_benchmarksql_error_log_level_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    if command[0] == "./runBenchmark.sh":
                        return CommandResult(" ".join(command), 0, "15:34:48,485 [main] ERROR  jTPCC : Term-00, Invalid number of terminals!\n", "")
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            status = load_yaml(root / "runs" / run.run_id / "status.json")
            self.assertEqual(status["status"], "failed")
            self.assertIn("Invalid number of terminals", status["last_error"])

    def test_run_benchmark_with_error_output_and_measured_tpmc_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")

                def fake_run(command, cwd, timeout, env=None):
                    if command[0] == "./runBenchmark.sh":
                        return CommandResult(
                            " ".join(command),
                            0,
                            "\n".join(
                                [
                                    '14:52:29,694 [Thread-63] ERROR  jTPCCTData : ERROR: duplicate key value violates unique constraint "bmsql_oorder_pkey"',
                                    "14:52:29,695 [Thread-63] INFO   jTPCC : Term-00, Measured tpmC (NewOrders) = 808.13",
                                    "14:52:29,695 [Thread-63] INFO   jTPCC : Term-00, Measured tpmTOTAL = 1805.54",
                                ]
                            ),
                            "",
                        )
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local

            status = load_yaml(root / "runs" / run.run_id / "status.json")
            progress = load_yaml(root / "runs/jobs/Job/job.json")
            published = load_yaml(root / "runs" / run.run_id / "result.json")
            self.assertEqual(status["status"], "success")
            self.assertEqual(progress["success_runs"], 1)
            self.assertEqual(progress["failed_runs"], 0)
            self.assertEqual(published["status"], "success")
            self.assertEqual(published["measured_tpmc"], 808.13)
            self.assertEqual(published["measured_tpmtotal"], 1805.54)

    def test_collectors_wrap_run_benchmark_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            self._enable_collectors(root)
            events: list[tuple[str, str]] = []
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            old_collector_manager = executor.CollectorManager

            class FakeCollectorManager:
                def __init__(self, root, target, run, config=None):
                    pass

                def start_phase(self, phase):
                    events.append(("start", phase))

                def stop_phase(self, phase):
                    events.append(("stop", phase))

            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")
                executor.CollectorManager = FakeCollectorManager

                def fake_run(command, cwd, timeout, env=None):
                    events.append(("run", command[0]))
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local
                executor.CollectorManager = old_collector_manager

            self.assertEqual(
                events,
                [
                    ("start", "runDatabaseBuild.sh"),
                    ("run", "./runDatabaseBuild.sh"),
                    ("stop", "runDatabaseBuild.sh"),
                    ("start", "runBenchmark.sh"),
                    ("run", "./runBenchmark.sh"),
                    ("stop", "runBenchmark.sh"),
                ],
            )

    def test_collector_error_fails_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, target, run = self._run_fixture(Path(tmp), skip_destroy=True)
            self._enable_collectors(root)
            old_prepare = executor._prepare_benchmark_run_dir
            old_probe = executor._probe_tpcc_objects
            old_run_local = executor._run_local
            old_collector_manager = executor.CollectorManager

            class FakeCollectorManager:
                def __init__(self, root, target, run, config=None):
                    pass

                def start_phase(self, phase):
                    return None

                def stop_phase(self, phase):
                    if phase == "runBenchmark.sh":
                        raise executor.CollectorError("perf export failed")

            try:
                executor._prepare_benchmark_run_dir = lambda root, target, run: None
                executor._probe_tpcc_objects = lambda target: CommandResult("probe", 0, "0\n", "")
                executor.CollectorManager = FakeCollectorManager

                def fake_run(command, cwd, timeout, env=None):
                    return CommandResult(" ".join(command), 0, "", "")

                executor._run_local = fake_run
                _execute_run(root, "Job", target, run)
            finally:
                executor._prepare_benchmark_run_dir = old_prepare
                executor._probe_tpcc_objects = old_probe
                executor._run_local = old_run_local
                executor.CollectorManager = old_collector_manager

            status = load_yaml(root / "runs" / run.run_id / "status.json")
            progress = load_yaml(root / "runs/jobs/Job/job.json")
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["phase"], "runBenchmark.sh")
            self.assertIn("collector error", status["last_error"])
            self.assertIn("perf export failed", progress["last_error"])

    def test_manual_parameter_commands_are_recorded_but_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self._profile()
            target = Target(profile, self._connection(), {}, {"max_connections": "128"}, False, {}, manual_parameter_commands=["pg_ctl restart -D /data"])
            job_dir = root / "runs/jobs/Job"
            job_dir.mkdir(parents=True)
            write_json(
                job_dir / "job.json",
                {
                    "job_id": "Job",
                    "status": "planned",
                    "targets": [{"target_id": profile.id, "status": "pending", "current_run": None, "current_phase": None}],
                    "failed_runs": 0,
                },
            )
            write_json(job_dir / "status.json", {"job_id": "Job", "status": "planned"})

            old_execution = executor._preflight_execution_host
            old_benchmark = executor._preflight_benchmarksql
            try:
                executor._preflight_execution_host = lambda targets: CommandResult("execution", 0, "", "")
                executor._preflight_benchmarksql = lambda root: CommandResult("benchmarksql", 0, "", "")

                executor.execute_job(root, "Job", [target], [])
            finally:
                executor._preflight_execution_host = old_execution
                executor._preflight_benchmarksql = old_benchmark

            status = load_yaml(job_dir / "status.json")
            self.assertEqual(status["status"], "success")
            timeline = (job_dir / "timeline.jsonl").read_text(encoding="utf-8")
            self.assertIn("manual_parameter_commands_declared", timeline)

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
        job_dir = root / "runs/jobs/Job"
        job_dir.mkdir(parents=True)
        write_json(
            job_dir / "job.json",
            {
                "job_id": "Job",
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

    def _enable_collectors(self, root: Path) -> None:
        path = root / "configs/collectors/default.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            """collectors:
  system:
    enabled: true
  perf:
    enabled: true
""",
            encoding="utf-8",
        )

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
