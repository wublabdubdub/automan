from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.clean_stale_runs import clean_stale_runs
from automan_core.config import load_yaml, write_json, write_yaml


class CleanStaleRunsTest(unittest.TestCase):
    def test_force_cleans_only_stale_running_run_and_updates_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, stale_run = self._write_job(root)
            stale_run_dir = root / "runs" / stale_run
            stale_work_dir = root / "work/tpcc/benchmarksql" / stale_run
            self.assertTrue(stale_run_dir.exists())
            self.assertTrue(stale_work_dir.exists())

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = clean_stale_runs(root, job_id=job_id, force=True, process_exists=lambda run_id: False)

            self.assertEqual(exit_code, 0)
            self.assertFalse(stale_run_dir.exists())
            self.assertFalse(stale_work_dir.exists())
            self.assertTrue((root / "runs" / f"{job_id}-pg-w100-c100").exists())
            self.assertTrue((root / "runs" / f"{job_id}-pg-w100-c500").exists())

            job_state = load_yaml(root / "runs/jobs" / job_id / "job.json")
            self.assertEqual(job_state["status"], "success")
            self.assertEqual(job_state["total_runs"], 2)
            self.assertEqual(job_state["finished_runs"], 2)
            self.assertEqual(job_state["success_runs"], 2)
            self.assertEqual(job_state["running_runs"], 0)
            self.assertEqual(job_state["pending_runs"], 0)
            self.assertEqual(job_state["failed_runs"], 0)
            self.assertIsNone(job_state["targets"][0]["current_run"])
            self.assertIsNone(job_state["targets"][0]["current_phase"])
            self.assertEqual(job_state["targets"][0]["total_runs"], 2)
            self.assertEqual(job_state["targets"][0]["finished_runs"], 2)

            plan = load_yaml(root / "runs/jobs" / job_id / "resolved-plan.yaml")
            self.assertEqual([run["run_id"] for run in plan["runs"]], [f"{job_id}-pg-w100-c100", f"{job_id}-pg-w100-c500"])

            text = output.getvalue()
            self.assertIn("[WARN] clean run:", text)
            self.assertIn("[ OK ] deleted run dir:", text)
            self.assertIn("[ OK ] updated job progress:", text)

    def test_force_cleans_failed_run_and_updates_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, failed_run = self._write_job(root)
            self._mark_run_failed(root, job_id, failed_run)
            failed_run_dir = root / "runs" / failed_run
            failed_work_dir = root / "work/tpcc/benchmarksql" / failed_run

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = clean_stale_runs(
                    root,
                    job_id=job_id,
                    force=True,
                    process_exists=lambda run_id: self.fail(f"failed run should not need a process check: {run_id}"),
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(failed_run_dir.exists())
            self.assertFalse(failed_work_dir.exists())

            job_state = load_yaml(root / "runs/jobs" / job_id / "job.json")
            self.assertEqual(job_state["status"], "success")
            self.assertEqual(job_state["total_runs"], 2)
            self.assertEqual(job_state["finished_runs"], 2)
            self.assertEqual(job_state["success_runs"], 2)
            self.assertEqual(job_state["failed_runs"], 0)

            text = output.getvalue()
            self.assertIn("status=failed", text)
            self.assertIn("reason=status=failed", text)

    def test_force_auto_detects_failed_job_without_job_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, failed_run = self._write_job(root)
            self._mark_run_failed(root, job_id, failed_run)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = clean_stale_runs(root, force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "runs" / failed_run).exists())
            self.assertIn("reason=status=failed", output.getvalue())

    def test_force_auto_cleans_multiple_failed_jobs_without_job_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job1, failed1 = self._write_job(root, "job1")
            job2, failed2 = self._write_job(root, "job2")
            self._mark_run_failed(root, job1, failed1)
            self._mark_run_failed(root, job2, failed2)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = clean_stale_runs(root, force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "runs" / failed1).exists())
            self.assertFalse((root / "runs" / failed2).exists())
            text = output.getvalue()
            self.assertIn("- job: job1", text)
            self.assertIn("- job: job2", text)

    def test_force_removes_job_dir_when_all_runs_are_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, _ = self._write_job(root)
            job_dir = root / "runs/jobs" / job_id
            plan = load_yaml(job_dir / "resolved-plan.yaml")
            failed_runs = [str(run["run_id"]) for run in plan["runs"]]
            for run_id in failed_runs:
                self._mark_run_failed(root, job_id, run_id)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = clean_stale_runs(root, job_id=job_id, force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse(job_dir.exists())
            for run_id in failed_runs:
                self.assertFalse((root / "runs" / run_id).exists())
                self.assertFalse((root / "work/tpcc/benchmarksql" / run_id).exists())
            self.assertIn("[ OK ] deleted job dir:", output.getvalue())

    def test_clean_requires_confirmation_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, stale_run = self._write_job(root)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = clean_stale_runs(
                    root,
                    job_id=job_id,
                    force=False,
                    process_exists=lambda run_id: False,
                    input_fn=lambda prompt: "no",
                )

            self.assertEqual(exit_code, 1)
            self.assertTrue((root / "runs" / stale_run).exists())
            self.assertIn("Type CLEAN to confirm", output.getvalue())
            self.assertIn("[FAIL] clean cancelled", output.getvalue())

    def test_no_stale_runs_succeeds_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_id, stale_run = self._write_job(root)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = clean_stale_runs(
                    root,
                    job_id=job_id,
                    force=False,
                    process_exists=lambda run_id: run_id == stale_run,
                    input_fn=lambda prompt: self.fail("clean should not prompt when nothing is stale"),
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("[ OK ] no stale or failed runs found", output.getvalue())

    def _write_job(self, root: Path, job_id: str = "job1") -> tuple[str, str]:
        run1 = f"{job_id}-pg-w100-c100"
        run2 = f"{job_id}-pg-w100-c500"
        run3 = f"{job_id}-pg-w100-c1000"
        job_dir = root / "runs/jobs" / job_id
        runs = [
            self._run(root, run1, 100, str(root / "runs" / run1), str(root / "work/tpcc/benchmarksql" / run1)),
            self._run(root, run2, 500, str(root / "runs" / run2), str(root / "work/tpcc/benchmarksql" / run2)),
            self._run(root, run3, 1000, str(root / "runs" / run3), str(root / "work/tpcc/benchmarksql" / run3)),
        ]
        plan = {
            "job_id": job_id,
            "benchmark": "tpcc",
            "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
            "runs": runs,
        }
        write_yaml(job_dir / "resolved-plan.yaml", plan)
        write_yaml(job_dir / "job.yaml", plan)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": "running",
                "total_runs": 3,
                "finished_runs": 2,
                "success_runs": 2,
                "running_runs": 1,
                "pending_runs": 0,
                "failed_runs": 0,
                "targets": [
                    {
                        "target_id": "pg",
                        "status": "running",
                        "current_run": run3,
                        "current_phase": "runBenchmark.sh",
                        "finished_runs": 2,
                        "total_runs": 3,
                    }
                ],
            },
        )
        write_json(job_dir / "status.json", {"job_id": job_id, "status": "running"})
        for run_id, status in ((run1, "success"), (run2, "success"), (run3, "running")):
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (root / "work/tpcc/benchmarksql" / run_id).mkdir(parents=True, exist_ok=True)
            write_json(run_dir / "status.json", {"run_id": run_id, "status": status, "phase": "runBenchmark.sh" if status == "running" else "report"})
        return job_id, run3

    def _mark_run_failed(self, root: Path, job_id: str, run_id: str) -> None:
        run_dir = root / "runs" / run_id
        write_json(run_dir / "status.json", {"run_id": run_id, "status": "failed", "phase": "runBenchmark.sh", "last_error": "boom"})
        job_dir = root / "runs/jobs" / job_id
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        statuses = [load_yaml(root / "runs" / str(run["run_id"]) / "status.json").get("status") for run in plan["runs"]]
        failed = sum(1 for status in statuses if status == "failed")
        success = sum(1 for status in statuses if status == "success")
        running = sum(1 for status in statuses if status == "running")
        total = len(statuses)
        job_status = "failed" if failed else "running" if running else "success"
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": job_status,
                "total_runs": total,
                "finished_runs": success + failed,
                "success_runs": success,
                "running_runs": running,
                "pending_runs": 0,
                "failed_runs": failed,
                "last_error": "boom" if failed else None,
                "targets": [
                    {
                        "target_id": "pg",
                        "status": job_status,
                        "current_run": None if not running else run_id,
                        "current_phase": None if not running else "runBenchmark.sh",
                        "finished_runs": success + failed,
                        "total_runs": total,
                        "last_error": "boom" if failed else None,
                    }
                ],
            },
        )
        write_json(job_dir / "status.json", {"job_id": job_id, "status": job_status})

    def _run(self, root: Path, run_id: str, terminals: int, run_dir: str, work_dir: str) -> dict:
        return {
            "run_id": run_id,
            "target_id": "pg",
            "warehouse": 100,
            "terminals": terminals,
            "run_mins": 15,
            "run_dir": run_dir,
            "work_dir": work_dir,
            "status_path": str(root / "runs" / run_id / "status.json"),
        }


if __name__ == "__main__":
    unittest.main()
