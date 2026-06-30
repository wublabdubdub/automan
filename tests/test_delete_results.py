from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.delete_results import delete_results
from automan_core.list_results import stable_result_id


class DeleteResultsTest(unittest.TestCase):
    def test_deletes_multiple_result_ids_and_updates_remaining_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100", "job1-pg-w100-c500"])
            self._write_job(root, "job2", ["job2-ym-ct1200-ts-write"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, ["pg-w100-c100", "job2-ym-ct1200-ts-write"], force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "runs/job1-pg-w100-c100").exists())
            self.assertFalse((root / "work/tpcc/benchmarksql/job1-pg-w100-c100").exists())
            self.assertTrue((root / "runs/job1-pg-w100-c500").exists())
            self.assertTrue((root / "work/tpcc/benchmarksql/job1-pg-w100-c500").exists())
            self.assertTrue((root / "runs/jobs/job1").exists())
            self.assertFalse((root / "runs/jobs/job1/report").exists())
            self.assertFalse((root / "runs/jobs/job2").exists())

            plan = load_yaml(root / "runs/jobs/job1/resolved-plan.yaml")
            self.assertEqual([run["run_id"] for run in plan["runs"]], ["job1-pg-w100-c500"])
            job_state = load_yaml(root / "runs/jobs/job1/job.json")
            self.assertEqual(job_state["total_runs"], 1)
            self.assertEqual(job_state["success_runs"], 1)
            self.assertEqual(job_state["status"], "success")

            text = output.getvalue()
            self.assertIn(f"deleted result: {stable_result_id('job1', 'job1-pg-w100-c100')}", text)
            self.assertIn(f"deleted result: {stable_result_id('job2', 'job2-ym-ct1200-ts-write')}", text)
            self.assertIn("deleted job dir:", text)

    def test_deletes_by_hash_id_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100", "job1-pg-w100-c500"])
            result_id = stable_result_id("job1", "job1-pg-w100-c500")

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, [result_id], force=True)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "runs/job1-pg-w100-c100").exists())
            self.assertFalse((root / "runs/job1-pg-w100-c500").exists())
            plan = load_yaml(root / "runs/jobs/job1/resolved-plan.yaml")
            self.assertEqual([run["run_id"] for run in plan["runs"]], ["job1-pg-w100-c100"])
            self.assertIn(f"deleted result: {result_id}", output.getvalue())

    def test_hash_id_distinguishes_same_run_id_in_different_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job_with_run_dir(root, "job1", "shared-run", root / "runs/job1-shared-run")
            self._write_job_with_run_dir(root, "job2", "shared-run", root / "runs/job2-shared-run")
            result_id = stable_result_id("job2", "shared-run")

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, [result_id], force=True)

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "runs/job1-shared-run").exists())
            self.assertFalse((root / "runs/job2-shared-run").exists())
            self.assertTrue((root / "runs/jobs/job1").exists())
            self.assertFalse((root / "runs/jobs/job2").exists())
            self.assertIn(f"deleted result: {result_id}", output.getvalue())

    def test_deletes_hash_id_for_discovered_tpch_result_not_in_plan_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id = "manual-sf10"
            run_id = "manual-sf10-final-ymatrix-tpch-query"
            job_dir = root / "runs/jobs" / job_id
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": job_id,
                    "benchmark": "tpch",
                    "targets": [{"id": "ymatrix", "connection": {"db_host": "127.0.0.1"}}],
                    "runs": [],
                },
            )
            write_json(job_dir / "job.json", {"job_id": job_id, "status": "success", "targets": []})
            write_json(job_dir / "status.json", {"job_id": job_id, "status": "success"})
            write_json(
                run_dir / "result.json",
                {
                    "run_id": run_id,
                    "target_id": "ymatrix",
                    "stage": "tpch-query",
                    "status": "success",
                },
            )
            result_id = stable_result_id(job_id, run_id)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, [result_id], force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse(run_dir.exists())
            self.assertFalse(job_dir.exists())
            self.assertIn(f"deleted result: {result_id}", output.getvalue())

    def test_delete_cancelled_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, ["pg-w100-c100"], input_fn=lambda prompt: "no")

            self.assertEqual(exit_code, 1)
            self.assertTrue((root / "runs/jobs/job1").exists())
            self.assertTrue((root / "runs/job1-pg-w100-c100").exists())
            self.assertIn("delete cancelled", output.getvalue())

    def test_delete_unknown_id_fails_without_deleting_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, ["pg-w100-c100", "missing"], force=True)

            self.assertEqual(exit_code, 1)
            self.assertTrue((root / "runs/job1-pg-w100-c100").exists())
            self.assertIn("result id not found: missing", output.getvalue())

    def test_delete_ambiguous_stable_id_fails_without_deleting_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100"])
            self._write_job(root, "job2", ["job2-pg-w100-c100"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_results(root, ["pg-w100-c100"], force=True)

            self.assertEqual(exit_code, 1)
            self.assertTrue((root / "runs/job1-pg-w100-c100").exists())
            self.assertTrue((root / "runs/job2-pg-w100-c100").exists())
            self.assertIn("result id is ambiguous: pg-w100-c100", output.getvalue())

    def test_refuses_to_delete_paths_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job1"
            job_dir.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job1",
                    "runs": [
                        {
                            "run_id": "job1-pg-w100-c100",
                            "run_dir": str(root / "runs/job1-pg-w100-c100"),
                            "work_dir": str(Path(tmp).parent / "outside-work"),
                        }
                    ],
                },
            )

            with self.assertRaises(ValueError):
                delete_results(root, ["pg-w100-c100"], force=True)

            self.assertTrue(job_dir.exists())

    def _write_job(self, root: Path, job_id: str, run_ids: list[str]) -> None:
        job_dir = root / "runs/jobs" / job_id
        runs = []
        for run_id in run_ids:
            terminals = 100 if run_id.endswith("c100") else 500
            run_dir = root / "runs" / run_id
            work_dir = root / "work/tpcc/benchmarksql" / run_id
            result_dir = run_dir / "benchmark/result"
            (result_dir / "data").mkdir(parents=True)
            work_dir.mkdir(parents=True)
            write_json(run_dir / "status.json", {"run_id": run_id, "status": "success"})
            runs.append(
                {
                    "run_id": run_id,
                    "target_id": "pg",
                    "warehouse": 100,
                    "terminals": terminals,
                    "run_mins": 15,
                    "run_dir": str(run_dir),
                    "work_dir": str(work_dir),
                    "status_path": str(run_dir / "status.json"),
                    "benchmark_result_dir": str(result_dir),
                }
            )
        plan = {
            "job_id": job_id,
            "matrix": {"warehouses": [100], "terminals": [100, 500], "load_workers": 32, "run_mins": 15},
            "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
            "runs": runs,
        }
        write_yaml(job_dir / "resolved-plan.yaml", plan)
        write_yaml(job_dir / "job.yaml", plan)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "success",
                "total_runs": len(runs),
                "finished_runs": len(runs),
                "success_runs": len(runs),
                "running_runs": 0,
                "failed_runs": 0,
                "pending_runs": 0,
                "targets": [{"target_id": "pg", "status": "success", "total_runs": len(runs), "finished_runs": len(runs)}],
            },
        )
        write_json(job_dir / "status.json", {"job_id": job_id, "status": "success"})
        (job_dir / "report").mkdir()

    def _write_job_with_run_dir(self, root: Path, job_id: str, run_id: str, run_dir: Path) -> None:
        job_dir = root / "runs/jobs" / job_id
        work_dir = root / "work/tpcc/benchmarksql" / f"{job_id}-{run_id}"
        run_dir.mkdir(parents=True)
        work_dir.mkdir(parents=True)
        write_json(run_dir / "status.json", {"run_id": run_id, "status": "success"})
        plan = {
            "job_id": job_id,
            "matrix": {"warehouses": [1], "terminals": [1], "load_workers": 1, "run_mins": 1},
            "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
            "runs": [
                {
                    "run_id": run_id,
                    "target_id": "pg",
                    "warehouse": 1,
                    "terminals": 1,
                    "run_mins": 1,
                    "run_dir": str(run_dir),
                    "work_dir": str(work_dir),
                    "status_path": str(run_dir / "status.json"),
                    "benchmark_result_dir": str(run_dir / "benchmark/result"),
                }
            ],
        }
        write_yaml(job_dir / "resolved-plan.yaml", plan)
        write_yaml(job_dir / "job.yaml", plan)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "success",
                "total_runs": 1,
                "finished_runs": 1,
                "success_runs": 1,
                "running_runs": 0,
                "failed_runs": 0,
                "pending_runs": 0,
                "targets": [{"target_id": "pg", "status": "success", "total_runs": 1, "finished_runs": 1}],
            },
        )
        write_json(job_dir / "status.json", {"job_id": job_id, "status": "success"})


if __name__ == "__main__":
    unittest.main()
