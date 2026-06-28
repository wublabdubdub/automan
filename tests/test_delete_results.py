from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import write_json, write_yaml
from automan_core.delete_results import delete_job


class DeleteResultsTest(unittest.TestCase):
    def test_deletes_job_and_all_referenced_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100", "job1-pg-w100-c500"])
            (root / "runs/collector/job1").mkdir(parents=True)
            (root / "runs/archives/job1").mkdir(parents=True)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_job(root, "job1", force=True)

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "runs/jobs/job1").exists())
            self.assertFalse((root / "runs/job1-pg-w100-c100").exists())
            self.assertFalse((root / "runs/job1-pg-w100-c500").exists())
            self.assertFalse((root / "work/tpcc/benchmarksql/job1-pg-w100-c100").exists())
            self.assertFalse((root / "work/tpcc/benchmarksql/job1-pg-w100-c500").exists())
            self.assertFalse((root / "runs/collector/job1").exists())
            self.assertFalse((root / "runs/archives/job1").exists())
            self.assertIn("deleted job: job1", output.getvalue())

    def test_delete_cancelled_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_job(root, "job1", ["job1-pg-w100-c100"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_job(root, "job1", input_fn=lambda prompt: "no")

            self.assertEqual(exit_code, 1)
            self.assertTrue((root / "runs/jobs/job1").exists())
            self.assertTrue((root / "runs/job1-pg-w100-c100").exists())
            self.assertIn("delete cancelled", output.getvalue())

    def test_delete_unknown_job_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = delete_job(root, "missing", force=True)

            self.assertEqual(exit_code, 1)
            self.assertIn("job not found: missing", output.getvalue())

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
                delete_job(root, "job1", force=True)

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
        write_json(job_dir / "job.json", {"job_id": job_id, "status": "success", "total_runs": len(runs)})
        write_json(job_dir / "status.json", {"job_id": job_id, "status": "success"})
        (job_dir / "report").mkdir()


if __name__ == "__main__":
    unittest.main()
