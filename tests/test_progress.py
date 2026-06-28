from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

from automan_core.config import write_json, write_yaml
from automan_core.progress import show_progress


class ProgressTest(unittest.TestCase):
    def test_auto_detects_running_job_and_prints_pigsty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            started = datetime.now() - timedelta(seconds=512)
            self._write_job(root, "job1", status="running", phase="runBenchmark.sh", phase_started_at=started)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_progress(root)

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("[ OK ] job: job1 status=running", text)
            self.assertIn("[ OK ] run: pg-w100-c500 target=pg host=127.0.0.1 wh=100 terminals=500", text)
            self.assertIn("phase: runBenchmark.sh action=test", text)
            self.assertIn("elapsed=00:08:", text)
            self.assertIn("expected=00:15:00", text)
            self.assertIn("remain=00:06:", text)
            self.assertIn("pct=", text)
            self.assertIn("[ OK ] total: 3 finished=1 running=1 pending=1 failed=0", text)
            self.assertIn("[HINT] completed results: ./automan list --job job1", text)

    def test_phase_actions_include_load_and_destroy(self) -> None:
        cases = [("runDatabaseBuild.sh", "action=load"), ("runDatabaseDestroy.sh", "action=destroy")]
        for phase, expected in cases:
            with self.subTest(phase=phase):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self._write_job(root, "job1", status="running", phase=phase, phase_started_at=datetime.now())

                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = show_progress(root)

                    self.assertEqual(exit_code, 0)
                    self.assertIn(f"phase: {phase} {expected}", output.getvalue())

    def test_no_running_job_shows_latest_job_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", status="success", phase="report", phase_started_at=datetime.now())

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_progress(root)

            text = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("[ OK ] job: job1 status=success", text)
            self.assertIn("[HINT] run: none active", text)
            self.assertIn("[HINT] completed results: ./automan list --job job1", text)

    def test_multiple_running_jobs_requires_job_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", status="running", phase="runBenchmark.sh", phase_started_at=datetime.now())
            self._write_job(root, "job2", status="running", phase="runBenchmark.sh", phase_started_at=datetime.now())

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_progress(root)

            text = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("[FAIL] multiple running TPC-C jobs found", text)
            self.assertIn("[HINT] job: job1", text)
            self.assertIn("[HINT] job: job2", text)

    def _write_job(self, root: Path, job_id: str, status: str, phase: str, phase_started_at: datetime) -> None:
        job_dir = root / "runs/jobs" / job_id
        run1 = f"{job_id}-pg-w100-c100"
        run2 = f"{job_id}-pg-w100-c500"
        run3 = f"{job_id}-pg-w100-c1000"
        plan = {
            "job_id": job_id,
            "benchmark": "tpcc",
            "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
            "runs": [
                {"run_id": run1, "target_id": "pg", "warehouse": 100, "terminals": 100, "run_mins": 15, "status_path": str(root / "runs" / run1 / "status.json")},
                {"run_id": run2, "target_id": "pg", "warehouse": 100, "terminals": 500, "run_mins": 15, "status_path": str(root / "runs" / run2 / "status.json")},
                {"run_id": run3, "target_id": "pg", "warehouse": 100, "terminals": 1000, "run_mins": 15, "status_path": str(root / "runs" / run3 / "status.json")},
            ],
        }
        write_yaml(job_dir / "resolved-plan.yaml", plan)
        write_json(
            job_dir / "job.json",
            {
                "job_id": job_id,
                "status": status,
                "total_runs": 3,
                "finished_runs": 1 if status == "running" else 3,
                "success_runs": 1 if status == "running" else 3,
                "running_runs": 1 if status == "running" else 0,
                "pending_runs": 1 if status == "running" else 0,
                "failed_runs": 0,
                "targets": [
                    {
                        "target_id": "pg",
                        "status": status,
                        "current_run": run2 if status == "running" else None,
                        "current_phase": phase if status == "running" else None,
                        "finished_runs": 1 if status == "running" else 3,
                        "total_runs": 3,
                    }
                ],
            },
        )
        write_json(job_dir / "status.json", {"job_id": job_id, "status": status})
        for run_id, run_status, run_phase in [(run1, "success", "report"), (run2, status, phase), (run3, "pending", None)]:
            write_json(
                root / "runs" / run_id / "status.json",
                {
                    "run_id": run_id,
                    "status": run_status,
                    "phase": run_phase,
                    "phase_started_at": phase_started_at.isoformat(),
                    "updated_at": datetime.now().isoformat(),
                },
            )


if __name__ == "__main__":
    unittest.main()
