from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automan_core import cli
from automan_core.checks import CheckResult
from automan_core.config import write_yaml


class CliContractTest(unittest.TestCase):
    def test_list_passes_refresh_size_inventory_to_result_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = root / "automan.yml"
            inventory.write_text("all: {}\n", encoding="utf-8")

            with patch.object(cli.Path, "cwd", return_value=root):
                with patch("sys.argv", ["automan", "list", "-t", "ts", "-i", str(inventory), "--refresh-size"]):
                    with patch("automan_core.cli.show_completed_results", return_value=0) as show:
                        cli.main()

        show.assert_called_once_with(root=root, job_id=None, benchmark_type="ts", refresh_size=True, inventory_path=inventory)

    def test_validate_prints_pigsty_style_status_tags(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "automan.yml"
            write_yaml(inventory, cli._compose_tpcc_inventory(repo, ["pg"]))
            output = io.StringIO()

            with patch("sys.argv", ["automan", "validate", "-i", str(inventory)]):
                with redirect_stdout(output):
                    cli.main()

        text = output.getvalue()
        self.assertIn("[ OK ]", text)
        self.assertIn("[HINT]", text)
        self.assertIn("manual-only", text)

    def test_param_prints_manual_commands_without_generating_shell_file(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_tpcc_inventory(repo, ["pg"]))
            output = io.StringIO()

            with patch("sys.argv", ["automan", "param", "-i", str(inventory), "--offline"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.main()

            text = output.getvalue()
            self.assertIn("[ OK ]", text)
            self.assertIn("printed only; no shell file is generated", text)
            self.assertIn("# Change commands", text)
            self.assertIn("ALTER SYSTEM SET max_connections", text)
            self.assertIn("# Confirm commands", text)
            self.assertIn("show max_connections", text)
            scripts = list((root / "runs" / "jobs").glob("*/manual-parameter-commands.sh"))
            self.assertEqual(scripts, [])

    def test_param_probes_postgresql_host_facts_before_rendering(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  vars:
    benchmark: tpcc
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
    pg:
      vars:
        db_type: postgresql
        storage_engine: heap
        test_mode: single_node
        tpcc_warehouses: [100]
        tpcc_terminals: [100, 500, 1000]
        tpcc_load_workers: 32
        tpcc_run_mins: 60
        db_host: 192.168.100.29
        db_port: 5232
        db_name: postgres
        db_user: zhangchen
        config_user: pgadmin
        host_facts:
          cpu_threads: 64
          memory_gb: 251
        database_parameters: {}
""",
                encoding="utf-8",
            )
            output = io.StringIO()

            def live_facts(_target):
                return {"cpu_threads": 64, "memory_gb": 63}

            with redirect_stdout(output):
                failures = cli.render_param_commands(root, inventory, fact_provider=live_facts)

        text = output.getvalue()
        self.assertEqual(failures, 0)
        self.assertIn("pg: probed host facts cpu_threads=64 memory_gb=63", text)
        self.assertIn("ALTER SYSTEM SET shared_buffers = '15GB';", text)
        self.assertIn("ALTER SYSTEM SET effective_cache_size = '44GB';", text)
        self.assertIn("ALTER SYSTEM SET work_mem = '15MB';", text)
        self.assertIn("ALTER SYSTEM SET maintenance_work_mem = '3GB';", text)
        self.assertNotIn("ALTER SYSTEM SET effective_cache_size = '175GB';", text)

    def test_param_fails_closed_when_postgresql_static_parameters_conflict_with_live_facts(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  vars:
    benchmark: tpcc
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
    pg:
      vars:
        db_type: postgresql
        storage_engine: heap
        test_mode: single_node
        tpcc_warehouses: [100]
        tpcc_terminals: [100, 500, 1000]
        tpcc_load_workers: 32
        tpcc_run_mins: 60
        db_host: 192.168.100.29
        db_port: 5232
        db_name: postgres
        db_user: zhangchen
        config_user: pgadmin
        database_parameters:
          shared_buffers: 62GB
          effective_cache_size: 175GB
""",
                encoding="utf-8",
            )
            output = io.StringIO()

            def live_facts(_target):
                return {"cpu_threads": 64, "memory_gb": 63}

            with redirect_stdout(output):
                failures = cli.render_param_commands(root, inventory, fact_provider=live_facts)

        text = output.getvalue()
        self.assertEqual(failures, 1)
        self.assertIn("[FAIL] pg: database_parameters conflict with live host facts", text)
        self.assertIn("shared_buffers=62GB (live recommendation 15GB)", text)
        self.assertIn("--offline", text)
        self.assertNotIn("ALTER SYSTEM SET shared_buffers", text)

    def test_param_probes_ymatrix_host_facts_before_rendering(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root, ["configs/database-profiles/ymatrix/heap-master-only.yaml"])
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  vars:
    benchmark: tpcc
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
    ymatrix_heap:
      vars:
        db_type: ymatrix
        storage_engine: heap
        test_mode: master_only
        tpcc_warehouses: [100]
        tpcc_terminals: [100, 500, 1000]
        tpcc_load_workers: 32
        tpcc_run_mins: 60
        db_host: 172.16.100.62
        db_port: 5432
        db_name: tpcc
        db_user: zhangchen
        config_user: mxadmin
        host_facts:
          cpu_threads: 64
          memory_gb: 251
        database_parameters: {}
""",
                encoding="utf-8",
            )
            output = io.StringIO()

            def live_facts(_target):
                return {"cpu_threads": 64, "memory_gb": 63}

            with redirect_stdout(output):
                failures = cli.render_param_commands(root, inventory, fact_provider=live_facts)

        text = output.getvalue()
        self.assertEqual(failures, 0)
        self.assertIn("probed host facts cpu_threads=64 memory_gb=63", text)
        self.assertIn("gpconfig -c shared_buffers -v 15GB", text)
        self.assertIn("gpconfig -c effective_cache_size -v 44GB", text)
        self.assertIn("gpconfig -c work_mem -v 15MB", text)
        self.assertIn("gpconfig -c maintenance_work_mem -v 3GB", text)
        self.assertNotIn("gpconfig -c effective_cache_size -v 175GB", text)

    def test_param_fails_closed_when_ymatrix_host_probe_fails(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root, ["configs/database-profiles/ymatrix/heap-master-only.yaml"])
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  vars:
    benchmark: tpcc
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
    ymatrix_heap:
      vars:
        db_type: ymatrix
        storage_engine: heap
        test_mode: master_only
        tpcc_warehouses: [100]
        tpcc_terminals: [100]
        tpcc_load_workers: 32
        tpcc_run_mins: 60
        db_host: 172.16.100.62
        db_port: 5432
        db_name: tpcc
        db_user: zhangchen
        config_user: mxadmin
        database_parameters: {}
""",
                encoding="utf-8",
            )
            output = io.StringIO()

            def broken_probe(_target):
                raise RuntimeError("host fact probe failed")

            with redirect_stdout(output):
                failures = cli.render_param_commands(root, inventory, fact_provider=broken_probe)

        text = output.getvalue()
        self.assertEqual(failures, 1)
        self.assertIn("[FAIL] ymatrix_heap: host fact probe failed", text)
        self.assertIn("--offline", text)
        self.assertNotIn("gpconfig -c shared_buffers", text)

    def test_param_offline_flag_is_explicit(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["param", "-i", "automan.yml", "--offline"])
        self.assertEqual(args.command, "param")
        self.assertTrue(args.offline)

    def test_configure_writes_single_target_from_alias(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_configure_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-c", "pg", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = (root / "automan.yml").read_text(encoding="utf-8")
            self.assertIn("pg:", inventory)
            self.assertNotIn("ymatrix_heap:", inventory)
            self.assertIn("configured targets: pg", output.getvalue())

    def test_configure_writes_multiple_targets_from_alias_list(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_configure_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-c", "pg, ym-heap", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = (root / "automan.yml").read_text(encoding="utf-8")
            self.assertIn("pg:", inventory)
            self.assertIn("ymatrix_heap:", inventory)
            self.assertIn("tpcc_warehouses:", inventory)
            self.assertIn("tpcc_warehouses: [100]", inventory)
            self.assertIn("tpcc_terminals: [100, 500]", inventory)
            self.assertIn("configured targets: pg, ym-heap", output.getvalue())

    def test_configure_accepts_legacy_single_target_name(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_configure_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-c", "tpcc/pg", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = (root / "automan.yml").read_text(encoding="utf-8")
            self.assertIn("pg:", inventory)
            self.assertIn("configured targets: pg", output.getvalue())

    def test_configure_rejects_unknown_alias_with_available_targets(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_configure_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-c", "not-a-target", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit):
                            cli.configure_main()

            text = output.getvalue()
            self.assertIn("[FAIL]", text)
            self.assertIn("available: pg, ym-heap, ym-mars3", text)

    def test_check_inventory_prints_check_results(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "automan.yml"
            write_yaml(inventory, cli._compose_tpcc_inventory(repo, ["pg"]))

            with patch("automan_core.cli.check_task_readiness", return_value=[CheckResult("FAIL", "perf missing"), CheckResult("HINT", "install perf")]):
                with redirect_stdout(output):
                    failures = cli.check_inventory(repo, inventory)

        text = output.getvalue()
        self.assertEqual(failures, 1)
        self.assertIn("[FAIL] perf missing", text)
        self.assertIn("[HINT] install perf", text)

    def test_legacy_run_task_remains_available(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--task", "configs/tasks/tpcc-postgresql-template.yaml", "--plan-only"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.task, "configs/tasks/tpcc-postgresql-template.yaml")
        self.assertTrue(args.plan_only)

    def test_run_command_accepts_stage(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["run", "-i", "automan.yml", "--stage", "load"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.inventory, "automan.yml")
        self.assertEqual(args.stage, "load")

        args = parser.parse_args(["run", "-i", "automan.yml", "--stage", "ap-query"])
        self.assertEqual(args.stage, "ap-query")

        args = parser.parse_args(["run", "-i", "automan.yml", "--stage", "tpch-query"])
        self.assertEqual(args.stage, "tpch-query")

        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "-i", "automan.yml", "--stage", "invalid"])

    def test_tpch_wrapper_declares_stage_contract(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "tpch.yml").read_text(encoding="utf-8")

        self.assertIn("tpch-load", text)
        self.assertIn("tpch-query", text)
        self.assertIn("playbooks/tpch.yml", text)

    def test_progress_command_accepts_watch_optional_seconds(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["progress", "--watch"])
        self.assertEqual(args.command, "progress")
        self.assertEqual(args.watch, 5)

        args = parser.parse_args(["progress", "--job", "job1", "--watch", "7"])
        self.assertEqual(args.job, "job1")
        self.assertEqual(args.watch, 7)

    def test_list_command_accepts_benchmark_type(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["list"])
        self.assertEqual(args.command, "list")
        self.assertEqual(args.type, "tpcc")

        args = parser.parse_args(["list", "-t", "ts", "--job", "job-ts"])
        self.assertEqual(args.type, "ts")
        self.assertEqual(args.job, "job-ts")

        args = parser.parse_args(["list", "-t", "ap", "--job", "job-ap"])
        self.assertEqual(args.type, "ap")
        self.assertEqual(args.job, "job-ap")

        args = parser.parse_args(["list", "-t", "tpch", "--job", "job-tpch"])
        self.assertEqual(args.type, "tpch")
        self.assertEqual(args.job, "job-tpch")

        with self.assertRaises(SystemExit):
            parser.parse_args(["list", "-t", "unknown"])

    def test_delete_command_uses_result_ids_and_force_flag(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["delete", "id1", "id2", "-f"])
        self.assertEqual(args.command, "delete")
        self.assertEqual(args.ids, ["id1", "id2"])
        self.assertTrue(args.force)

    def test_delete_command_does_not_accept_yes_alias(self) -> None:
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["delete", "pg-w100-c500", "--yes"])

    def test_clean_command_accepts_job_and_force(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["clean", "--job", "job1", "-f"])
        self.assertEqual(args.command, "clean")
        self.assertEqual(args.job, "job1")
        self.assertTrue(args.force)

    def test_report_missing_job_prints_fail_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = io.StringIO()

            with patch("sys.argv", ["automan", "report", "--job", "missing"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit):
                            cli.main()

            text = output.getvalue()
            self.assertIn("[FAIL]", text)
            self.assertIn("job missing", text)

    def test_cleanup_command_is_removed(self) -> None:
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["cleanup", "-i", "automan.yml"])


def _copy_minimal_repo_files(src: Path, dst: Path, extra_files: list[str] | None = None) -> None:
    for relative in [
        "configs/database-profiles/postgresql/heap-single-node.yaml",
        "benchmarks/tpcc/benchmarksql/props.template",
        *(extra_files or []),
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_configure_templates(src: Path, dst: Path) -> None:
    for relative in [
        "conf/tpcc/base.yml",
        "conf/tpcc/targets/pg.yml",
        "conf/tpcc/targets/ym-heap.yml",
        "conf/tpcc/targets/ym-mars3.yml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
