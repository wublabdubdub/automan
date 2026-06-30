from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automan_core import cli
from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.list_results import completed_result_rows, show_completed_results
from automan_core.task_runner import load_task_definition, run_task_job, validate_task_definition
from automan_core.ssh import CommandResult
from automan_core.tpch import build_tpch_run_specs, execute_tpch_job


class TpchBenchmarkTest(unittest.TestCase):
    def test_configure_tpch_generates_pg_heap_and_mars3_inventory(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-t", "tpch", "-c", "pg,ym-heap,ym-mars3", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = load_yaml(root / "automan.yml")
            self.assertEqual(inventory["all"]["vars"]["benchmark"], "tpch")
            self.assertEqual(inventory["all"]["vars"]["tpch_stages"], ["tpch-load", "tpch-query"])
            self.assertEqual(inventory["all"]["vars"]["tpch"]["scale_factors"], [1])
            self.assertEqual(inventory["all"]["children"]["pg"]["vars"]["tpch_ddl_profile"], "pg")
            self.assertEqual(inventory["all"]["children"]["ymatrix_heap"]["vars"]["tpch_ddl_profile"], "ym-heap")
            self.assertEqual(inventory["all"]["children"]["ymatrix_mars3"]["vars"]["tpch_ddl_profile"], "ym-mars3")
            self.assertIn("configured targets: pg, ym-heap, ym-mars3", output.getvalue())

    def test_tpch_config_loads_data_prepare_defaults(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)

            self.assertEqual(task.tpch_config.data_prepare.mode, "auto")
            self.assertEqual(task.tpch_config.data_prepare.generator, "dbgen")
            self.assertEqual(task.tpch_config.data_prepare.source_dir, "tools/tpch-dbgen")
            self.assertEqual(task.tpch_config.data_prepare.build_command, "make")
            self.assertEqual(task.tpch_config.data_prepare.dbgen_command, "./dbgen")
            self.assertFalse(task.tpch_config.data_prepare.force)

    def test_tpch_config_defaults_to_ymatrix_backend(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)

            self.assertEqual(task.tpch_config.backend.type, "ymatrix-tpch")
            self.assertEqual(task.tpch_config.backend.source_dir, "tools/ymatrix-tpch")
            self.assertEqual(task.tpch_config.backend.remote_dir, "runs/{run_id}/ymatrix-tpch")
            self.assertEqual(task.tpch_config.backend.database_type, "matrixdb")
            self.assertEqual(task.tpch_config.backend.access_method, "mars3")
            self.assertEqual(task.tpch_config.backend.load_data_type, "mxgate")
            self.assertEqual(task.tpch_config.backend.optimizer, "off")
            self.assertTrue(task.tpch_config.backend.preheating_data)
            self.assertFalse(task.tpch_config.backend.explain_analyze)

    def test_ymatrix_backend_stage_flags_and_storage_rendering(self) -> None:
        from automan_core.tpch_backend import render_mars3_storage, stage_flags

        self.assertEqual(render_mars3_storage(1200), "USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=1200)")
        self.assertEqual(stage_flags("tpch-load")["RUN_LOAD"], "true")
        self.assertEqual(stage_flags("tpch-load")["RUN_SQL"], "false")
        self.assertEqual(stage_flags("tpch-query")["RUN_LOAD"], "false")
        self.assertEqual(stage_flags("tpch-query")["RUN_SQL"], "true")

    def test_ymatrix_backend_variables_use_target_db_host(self) -> None:
        from automan_core.tpch_backend import remote_backend_dir, render_backend_variables

        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["children"]["ymatrix_mars3"]["vars"]["db_host"] = "10.9.8.7"
            data["all"]["children"]["ymatrix_mars3"]["vars"]["config_workdir"] = "/home/mxadmin/automan"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")

            remote_dir = remote_backend_dir(task.targets[0], task.tpch_config.backend, runs[0])
            variables = render_backend_variables(task.targets[0], task.tpch_config.backend, runs[0])

            self.assertEqual(remote_dir, f"/home/mxadmin/automan/runs/{runs[0].run_id}/ymatrix-tpch")
            self.assertIn('export PGHOST="10.9.8.7"', variables)
            self.assertIn('GEN_DATA_SCALE="1"', variables)
            self.assertIn('LOAD_DATA_TYPE="mxgate"', variables)
            self.assertIn('SMALL_STORAGE="USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=1200)"', variables)
            self.assertIn("TPCH_SESSION_GUCS=\"set statement_mem to '1GB';\"", variables)
            self.assertIn('PURE_SCRIPT_MODE=""', variables)
            self.assertIn('RUN_LOAD="true"', variables)
            self.assertIn('RUN_SQL="false"', variables)

    def test_vendored_ymatrix_tpch_backend_is_present(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        backend = repo / "tools/ymatrix-tpch"

        self.assertTrue((backend / "tpch.sh").exists())
        self.assertTrue((backend / "rollout.sh").exists())
        self.assertTrue((backend / "00_compile_tpch/dbgen/release.h").exists())
        text = (backend / "00_compile_tpch/dbgen/release.h").read_text(encoding="utf-8")
        self.assertIn("#define VERSION 2", text)
        self.assertIn("#define RELEASE 17", text)
        self.assertIn("#define PATCH 0", text)
        self.assertIn("#define BUILD 0", text)
        self.assertIn("11bc7c50910f4bdb732a4615e7711beaa4914965", (backend / "AUTOMAN_VENDOR.md").read_text(encoding="utf-8"))

    def test_remote_helpers_are_available_for_backend(self) -> None:
        from automan_core.remote import RemoteClient

        self.assertTrue(hasattr(RemoteClient, "run"))
        self.assertTrue(hasattr(RemoteClient, "upload_dir"))
        self.assertTrue(hasattr(RemoteClient, "download_dir"))

    def test_execute_tpch_uses_ymatrix_backend_by_default(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["children"]["ymatrix_mars3"]["vars"]["db_host"] = "10.9.8.7"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")
            backend = FakeYMatrixBackend()
            commands: list[list[str]] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                commands.append(command)
                return CommandResult("unexpected", 1, "", "old backend should not run")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner, backend_executor=backend)

            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(commands, [])
            self.assertEqual(backend.calls, [("10.9.8.7", "tpch-load", 1200)])
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["backend_type"], "ymatrix-tpch")
            self.assertEqual(result["schema"], "tpch")
            self.assertIn("ymatrix-tpch", result["remote_backend_dir"])

    def test_ymatrix_backend_uploads_tarball_instead_of_recursive_sftp(self) -> None:
        from automan_core.tpch_backend import YMatrixTpchBackend

        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            source_dir = root / "tools/ymatrix-tpch"
            source_dir.mkdir(parents=True)
            (source_dir / "tpch.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "rollout.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "nested").mkdir()
            (source_dir / "nested" / "file.txt").write_text("payload", encoding="utf-8")
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["children"]["ymatrix_mars3"]["vars"]["db_host"] = "10.9.8.7"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")
            remote = FakeRemoteClient()
            backend = YMatrixTpchBackend(remote_factory=lambda target: remote)

            result = backend.run(root, task.targets[0], task.tpch_config, runs[0])

            self.assertEqual(result["status"], "success")
            self.assertFalse(remote.upload_dir_called)
            self.assertTrue(any(call[0].name == "ymatrix-tpch.tar.gz" and call[1].endswith("/ymatrix-tpch.tar.gz") for call in remote.upload_file_calls))
            self.assertTrue(any("tar -xzf ymatrix-tpch.tar.gz" in command for command in remote.run_commands))
            self.assertTrue(any("sed -i 's/\\r$//'" in command and "dists.dss" in command for command in remote.run_commands))
            self.assertTrue(any("./rollout.sh" in command for command in remote.run_commands))
            self.assertTrue(any("seq 1 60" in command and "select 1" in command for command in remote.run_commands))
            self.assertTrue(any("mkdir -p" in command and "/generated/log" in command for command in remote.run_commands))
            self.assertTrue(any("gp_segment_configuration" in command and 'ssh -o StrictHostKeyChecking=no "$host" mkdir -p' in command for command in remote.run_commands))
            self.assertTrue(any("GPHOME" in command and "greenplum_path.sh" in command for command in remote.run_commands))
            self.assertFalse(any("bash ./tpch.sh" in command for command in remote.run_commands))

    def test_tpch_inventory_validates_and_plans_three_ddl_profiles(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg", "ym-heap", "ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200, 4096]
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)
            messages = validate_task_definition(task)
            job_dir = run_task_job(root, inventory, plan_only=True)
            plan = load_yaml(job_dir / "resolved-plan.yaml")

            self.assertEqual(task.benchmark, "tpch")
            self.assertTrue(any(message.text == "benchmark: tpch" and message.level == "OK" for message in messages))
            self.assertEqual(plan["benchmark"], "tpch")
            self.assertEqual({target["tpch_ddl_profile"] for target in plan["targets"]}, {"pg", "ym-heap", "ym-mars3"})
            self.assertEqual(len(plan["runs"]), 8)
            self.assertEqual(
                [(run["target_id"], run["ddl_profile"], run["compress_threshold"], run["stage"]) for run in plan["runs"]],
                [
                    ("pg", "pg", None, "tpch-load"),
                    ("pg", "pg", None, "tpch-query"),
                    ("ymatrix_heap", "ym-heap", None, "tpch-load"),
                    ("ymatrix_heap", "ym-heap", None, "tpch-query"),
                    ("ymatrix_mars3", "ym-mars3", 1200, "tpch-load"),
                    ("ymatrix_mars3", "ym-mars3", 1200, "tpch-query"),
                    ("ymatrix_mars3", "ym-mars3", 4096, "tpch-load"),
                    ("ymatrix_mars3", "ym-mars3", 4096, "tpch-query"),
                ],
            )

    def test_tpch_single_stage_plan_keeps_all_ddl_profiles(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg", "ym-heap", "ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200]
            write_yaml(inventory, data)

            job_dir = run_task_job(root, inventory, plan_only=True, stage="tpch-query")
            plan = load_yaml(job_dir / "resolved-plan.yaml")

            self.assertEqual([run["stage"] for run in plan["runs"]], ["tpch-query", "tpch-query", "tpch-query"])
            self.assertEqual([run["ddl_profile"] for run in plan["runs"]], ["pg", "ym-heap", "ym-mars3"])

    def test_tpch_run_id_includes_run_mins(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["tpch"]["run_mins"] = [0, 10]
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-query")

            run_ids = [run.run_id for run in runs]
            self.assertEqual(len(run_ids), 2)
            self.assertEqual(len(set(run_ids)), 2)
            self.assertIn("job-tpch-pg-sf1-q1-m0-tpch-query", run_ids)
            self.assertIn("job-tpch-pg-sf1-q1-m10-tpch-query", run_ids)

    def test_tpch_results_list_uses_tpch_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job-tpch"
            run_dir = root / "runs/job-tpch-ymatrix_mars3-ct1200-sf1-q1-tpch-query"
            run_dir.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job-tpch",
                    "benchmark": "tpch",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "172.16.100.62"}}],
                    "runs": [
                        {
                            "run_id": run_dir.name,
                            "target_id": "ymatrix_mars3",
                            "stage": "tpch-query",
                            "run_dir": str(run_dir),
                        }
                    ],
                },
            )
            write_json(
                run_dir / "result.json",
                {
                    "run_id": run_dir.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "tpch-query",
                    "status": "success",
                    "ddl_profile": "ym-mars3",
                    "compress_threshold": 1200,
                    "scale_factor": 1,
                    "query_streams": 1,
                    "run_mins": 0,
                    "table_data_size": "1.5 GB",
                    "elapsed_seconds": 123.4,
                    "qphh": 88.8,
                    "query_count": 22,
                    "avg_ms": 5600.0,
                    "p95_ms": 9000.0,
                    "errors": 0,
                    "session_end": "2026-06-29T18:30:00",
                },
            )

            rows = completed_result_rows(root, "job-tpch", benchmark_type="tpch")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "job-tpch", benchmark_type="tpch")

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["benchmark"], "tpch")
            self.assertEqual(rows[0]["ddl_profile"], "ym-mars3")
            text = output.getvalue()
            self.assertIn("DDL Profile", text)
            self.assertIn("Queries/h", text)
            self.assertIn("Table Data Size", text)
            self.assertNotIn("tpmC", text)

    def test_tpch_results_list_includes_failed_matrix_rows_for_troubleshooting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job-tpch"
            run_dir = root / "runs/job-tpch-pg-sf1-q1-m0-tpch-load"
            run_dir.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job-tpch",
                    "benchmark": "tpch",
                    "targets": [{"id": "pg", "connection": {"db_host": "192.168.100.29"}}],
                    "runs": [
                        {
                            "run_id": run_dir.name,
                            "target_id": "pg",
                            "stage": "tpch-load",
                            "run_dir": str(run_dir),
                        }
                    ],
                },
            )
            write_json(
                run_dir / "result.json",
                {
                    "run_id": run_dir.name,
                    "target_id": "pg",
                    "stage": "tpch-load",
                    "status": "failed",
                    "ddl_profile": "pg",
                    "scale_factor": 1,
                    "query_streams": 1,
                    "run_mins": 0,
                    "error": "TPC-H data directory not found",
                    "session_end": "2026-06-29T18:30:00",
                },
            )

            rows = completed_result_rows(root, "job-tpch", benchmark_type="tpch")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "job-tpch", benchmark_type="tpch")

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "failed")
            self.assertEqual(rows[0]["error"], "TPC-H data directory not found")
            text = output.getvalue()
            self.assertIn("failed", text)
            self.assertIn("TPC-H data directory not found", text)

    def test_execute_tpch_load_creates_result_from_tbl_files(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            _copy_tpch_assets(repo, root)
            _write_minimal_tpch_data(root / "benchmarks/tpch/data/sf1")
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                sql = command[-1]
                if "pg_total_relation_size" in sql:
                    return CommandResult("psql", 0, "128 MB|134217728\n", "")
                return CommandResult("psql", 0, "ok\n", "")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner)

            job_state = load_yaml(root / "runs/jobs/job-tpch/job.json")
            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(job_state["status"], "success")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["loaded_tables"], 8)
            self.assertEqual(result["ddl_profile"], "ym-mars3")
            self.assertEqual(result["compress_threshold"], 1200)
            self.assertEqual(result["table_data_size"], "128 MB")

    def test_execute_tpch_load_missing_data_fails_before_ddl(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            _copy_tpch_assets(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            data["all"]["vars"]["tpch"]["data_prepare"]["mode"] = "existing"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")
            commands: list[list[str]] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                commands.append(command)
                return CommandResult("psql", 0, "ok\n", "")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner)

            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(result["status"], "failed")
            self.assertIn("TPC-H data file(s) missing", result["error"])
            self.assertEqual(commands, [])

    def test_execute_tpch_load_empty_data_fails_before_ddl(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            _copy_tpch_assets(repo, root)
            data_dir = root / "benchmarks/tpch/data/sf1"
            data_dir.mkdir(parents=True)
            for table in ["region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem"]:
                (data_dir / f"{table}.tbl").write_text("", encoding="utf-8")
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            data["all"]["vars"]["tpch"]["data_prepare"]["mode"] = "existing"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")
            commands: list[list[str]] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                commands.append(command)
                return CommandResult("psql", 0, "ok\n", "")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner)

            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(result["status"], "failed")
            self.assertIn("empty TPC-H data file(s)", result["error"])
            self.assertEqual(commands, [])

    def test_execute_tpch_load_auto_generates_missing_data(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            _copy_tpch_assets(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")
            commands: list[list[str]] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                commands.append(command)
                if command[:2] == ["sh", "-lc"] and "dbgen" in command[2]:
                    _write_minimal_tpch_data(cwd)
                    return CommandResult("dbgen", 0, "generated\n", "")
                sql = command[-1]
                if "pg_total_relation_size" in sql:
                    return CommandResult("psql", 0, "128 MB|134217728\n", "")
                return CommandResult("psql", 0, "ok\n", "")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner)

            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(result["status"], "success")
            self.assertTrue((root / "benchmarks/tpch/data/sf1/data-manifest.json").exists())
            self.assertTrue(any(command[:2] == ["sh", "-lc"] and "dbgen" in command[2] for command in commands))

    def test_execute_tpch_query_runs_22_sql_files_and_writes_qphh(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_tpch_runtime_files(repo, root)
            _copy_tpch_assets(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["job_id"] = "job-tpch"
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-query")

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                sql = command[-1]
                if "pg_total_relation_size" in sql:
                    return CommandResult("psql", 0, "128 MB|134217728\n", "")
                return CommandResult("psql", 0, "row\n", "")

            execute_tpch_job(root, "job-tpch", task.targets, task.tpch_config, runs, task.collectors, runner=runner)

            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["query_count"], 22)
            self.assertEqual(result["rows_returned"], 22)
            self.assertGreater(result["qphh"], 0)
            self.assertEqual(result["queries_per_hour"], result["qphh"])
            self.assertTrue((runs[0].benchmark_dir / "outputs" / "stream001-q01.out").exists())


def _copy_tpch_templates(src: Path, dst: Path) -> None:
    for relative in [
        "conf/tpch/base.yml",
        "conf/tpch/targets/pg.yml",
        "conf/tpch/targets/ym-heap.yml",
        "conf/tpch/targets/ym-mars3.yml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_tpch_runtime_files(src: Path, dst: Path) -> None:
    _copy_tpch_templates(src, dst)
    for relative in [
        "configs/database-profiles/postgresql/heap-single-node.yaml",
        "configs/database-profiles/ymatrix/heap-master-only.yaml",
        "configs/database-profiles/ymatrix/mars3-master-only.yaml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_tpch_assets(src: Path, dst: Path) -> None:
    for source in [
        *(src / "benchmarks/tpch/schema/pg").glob("*.sql"),
        *(src / "benchmarks/tpch/schema/ym-heap").glob("*.sql"),
        *(src / "benchmarks/tpch/schema/ym-mars3").glob("*.sql"),
        *(src / "benchmarks/tpch/queries/standard").glob("*.sql"),
    ]:
        target = dst / source.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_minimal_tpch_data(data_dir: Path) -> None:
    rows = {
        "region": "0|AFRICA|comment|\n",
        "nation": "0|ALGERIA|0|comment|\n",
        "supplier": "1|Supplier#1|address|0|00-000|1.00|comment|\n",
        "customer": "1|Customer#1|address|0|00-000|1.00|BUILDING|comment|\n",
        "part": "1|part|MFGR|Brand#12|PROMO BURNISHED COPPER|1|SM BOX|1.00|comment|\n",
        "partsupp": "1|1|1|1.00|comment|\n",
        "orders": "1|1|F|1.00|1994-01-01|1-URGENT|Clerk#1|0|comment|\n",
        "lineitem": "1|1|1|1|1.00|1.00|0.05|0.01|R|F|1994-01-02|1994-01-03|1994-01-04|DELIVER IN PERSON|AIR|comment|\n",
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    for table, text in rows.items():
        (data_dir / f"{table}.tbl").write_text(text, encoding="utf-8")


class FakeYMatrixBackend:
    def __init__(self) -> None:
        self.calls = []

    def run(self, root, target, config, run):
        self.calls.append((target.connection.db_host, run.stage, run.compress_threshold))
        return {
            "run_id": run.run_id,
            "target_id": run.target_id,
            "stage": run.stage,
            "status": "success",
            "error": None,
            "backend_type": "ymatrix-tpch",
            "remote_backend_dir": f"{target.connection.remote_workdir}/runs/{run.run_id}/ymatrix-tpch",
            "schema": "tpch",
            "scale_factor": run.scale_factor,
            "compress_threshold": run.compress_threshold,
            "query_streams": run.query_streams,
            "run_mins": run.run_mins,
            "elapsed_seconds": 1.0,
            "upstream_artifacts": {"local_dir": str(run.benchmark_dir / "upstream")},
        }


class FakeRemoteClient:
    def __init__(self) -> None:
        self.run_commands: list[str] = []
        self.upload_file_calls: list[tuple[Path, str]] = []
        self.upload_dir_called = False

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        self.run_commands.append(command)
        return CommandResult(command, 0, "ok\n", "")

    def upload_file(self, local_path: Path, remote_path: str) -> CommandResult:
        self.upload_file_calls.append((local_path, remote_path))
        return CommandResult(f"sftp put {local_path} {remote_path}", 0, remote_path, "")

    def upload_dir(self, local_dir: Path, remote_dir: str) -> CommandResult:
        self.upload_dir_called = True
        return CommandResult(f"sftp put -r {local_dir} {remote_dir}", 0, remote_dir, "")

    def download_dir(self, remote_dir: str, local_dir: Path) -> CommandResult:
        local_dir.mkdir(parents=True, exist_ok=True)
        return CommandResult(f"sftp get -r {remote_dir} {local_dir}", 0, str(local_dir), "")


if __name__ == "__main__":
    unittest.main()
