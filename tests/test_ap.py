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
from automan_core.ap import build_ap_run_specs, execute_ap_job
from automan_core.ssh import CommandResult


class ApBenchmarkTest(unittest.TestCase):
    def test_configure_ap_ym_mars3_generates_inventory(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ap_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-t", "ap", "-c", "ym-mars3", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = load_yaml(root / "automan.yml")
            self.assertEqual(inventory["all"]["vars"]["benchmark"], "ap")
            self.assertEqual(inventory["all"]["vars"]["ap_stages"], ["ap-query"])
            self.assertEqual(inventory["all"]["vars"]["compress_threshold"], [1200, 4096, 8192, 32768])
            self.assertEqual(inventory["all"]["vars"]["ap_query"]["query_set"], "vehicle_ap_basic")
            self.assertIn("ymatrix_mars3", inventory["all"]["children"])
            self.assertIn("configured targets: ym-mars3", output.getvalue())

    def test_ap_inventory_validates_and_plans_threshold_matrix(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ap_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ap", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ap"
            data["all"]["vars"]["compress_threshold"] = [1200, 4096]
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)
            messages = validate_task_definition(task)
            job_dir = run_task_job(root, inventory, plan_only=True)
            plan = load_yaml(job_dir / "resolved-plan.yaml")

            self.assertEqual(task.benchmark, "ap")
            self.assertTrue(any(message.text == "benchmark: ap" and message.level == "OK" for message in messages))
            self.assertEqual(plan["benchmark"], "ap")
            self.assertEqual(
                [(run["compress_threshold"], run["stage"], run["source_table"]) for run in plan["runs"]],
                [
                    (1200, "ap-query", "iot_vehicle_raw_ct1200"),
                    (4096, "ap-query", "iot_vehicle_raw_ct4096"),
                ],
            )

    def test_ap_run_id_includes_warmup_rounds(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ap_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ap", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ap"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["ap_query"]["rounds"] = [3]
            data["all"]["vars"]["ap_query"]["warmup_rounds"] = [0, 1]
            write_yaml(inventory, data)

            task = load_task_definition(root, inventory)
            runs = build_ap_run_specs(root, "job-ap", task.targets, task.ap_config, "ap-query")

            run_ids = [run.run_id for run in runs]
            self.assertEqual(len(run_ids), 2)
            self.assertEqual(len(set(run_ids)), 2)
            self.assertIn("job-ap-ymatrix_mars3-ct1200-ap-query-r3-w0", run_ids)
            self.assertIn("job-ap-ymatrix_mars3-ct1200-ap-query-r3-w1", run_ids)

    def test_ap_results_list_uses_ap_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job-ap"
            run_dir = root / "runs/job-ap-ymatrix_mars3-ct1200-ap-query-r3"
            run_dir.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job-ap",
                    "benchmark": "ap",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "172.16.100.62"}}],
                    "runs": [
                        {
                            "run_id": run_dir.name,
                            "target_id": "ymatrix_mars3",
                            "stage": "ap-query",
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
                    "stage": "ap-query",
                    "status": "success",
                    "source_table": "iot_vehicle_raw_ct1200",
                    "compress_threshold": 1200,
                    "table_data_size": "289 MB",
                    "rounds": 3,
                    "query_count": 9,
                    "avg_ms": 80.1,
                    "p50_ms": 79.9,
                    "p95_ms": 85.0,
                    "p99_ms": 86.0,
                    "rows_returned": 9000,
                    "errors": 0,
                    "session_end": "2026-06-29T18:10:00",
                },
            )

            rows = completed_result_rows(root, "job-ap", benchmark_type="ap")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "job-ap", benchmark_type="ap")

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["benchmark"], "ap")
            self.assertEqual(rows[0]["source_table"], "iot_vehicle_raw_ct1200")
            text = output.getvalue()
            self.assertIn("Source Table", text)
            self.assertIn("Rows Returned", text)
            self.assertIn("Compress Threshold", text)
            self.assertNotIn("tpmC", text)

    def test_execute_ap_job_runs_query_files_and_writes_result(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ap_runtime_files(repo, root)
            _copy_ap_queries(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ap", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ap"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["collectors"]["enabled"] = False
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_ap_run_specs(root, "job-ap", task.targets, task.ap_config, "ap-query")

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                sql = command[-1]
                if "pg_total_relation_size" in sql:
                    return CommandResult("psql", 0, "10 MB|10485760\n", "")
                return CommandResult("psql", 0, "row1\nrow2\n", "")

            execute_ap_job(root, "job-ap", task.targets, task.ap_config, runs, task.collectors, runner=runner)

            job_state = load_yaml(root / "runs/jobs/job-ap/job.json")
            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(job_state["status"], "success")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["query_count"], 15)
            self.assertEqual(result["rows_returned"], 30)
            self.assertEqual(result["table_data_size"], "10 MB")
            self.assertTrue((runs[0].benchmark_dir / "outputs" / "run001-01_speed_by_hour.out").exists())


def _copy_ap_templates(src: Path, dst: Path) -> None:
    for relative in [
        "conf/ap/base.yml",
        "conf/ap/targets/ym-mars3.yml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_ap_runtime_files(src: Path, dst: Path) -> None:
    _copy_ap_templates(src, dst)
    for relative in [
        "configs/database-profiles/ymatrix/mars3-master-only.yaml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_ap_queries(src: Path, dst: Path) -> None:
    for source in (src / "benchmarks/ap/queries/vehicle_ap_basic").glob("*.sql"):
        target = dst / "benchmarks/ap/queries/vehicle_ap_basic" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
