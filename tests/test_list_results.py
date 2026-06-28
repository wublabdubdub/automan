from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import write_json, write_yaml
from automan_core.list_results import completed_result_rows, show_completed_results


class ListResultsTest(unittest.TestCase):
    def test_lists_completed_run_even_when_job_is_still_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job1"
            run1 = root / "runs/job1-pg-w100-c100"
            run2 = root / "runs/job1-pg-w100-c500"
            result_dir = run1 / "benchmark/result"
            (result_dir / "data").mkdir(parents=True)
            run2.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job1",
                    "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
                    "runs": [
                        {
                            "run_id": "job1-pg-w100-c100",
                            "target_id": "pg",
                            "warehouse": 100,
                            "terminals": 100,
                            "run_mins": 15,
                            "run_dir": str(run1),
                            "benchmark_result_dir": str(result_dir),
                        },
                        {
                            "run_id": "job1-pg-w100-c500",
                            "target_id": "pg",
                            "warehouse": 100,
                            "terminals": 500,
                            "run_mins": 15,
                            "run_dir": str(run2),
                            "benchmark_result_dir": str(run2 / "benchmark/result"),
                        },
                    ],
                },
            )
            write_json(job_dir / "job.json", {"job_id": "job1", "status": "running"})
            write_json(run1 / "status.json", {"run_id": "job1-pg-w100-c100", "status": "success"})
            write_json(run2 / "status.json", {"run_id": "job1-pg-w100-c500", "status": "pending"})
            (result_dir / "data/tx_summary.csv").write_text("tpmC,123.45\ntpmTOTAL,456.78\n", encoding="utf-8")
            (result_dir / "data/runInfo.csv").write_text(
                "run,driver,driverVersion,db,sessionStart,runMins,loadWarehouses,runWarehouses,numSUTThreads,limitTxnsPerMin,thinkTimeMultiplier,keyingTimeMultiplier\n"
                "1,simple,5.1devel,postgres,2026-06-28 10:00:00,15,100,100,100,10000000,1.0,1.0\n",
                encoding="utf-8",
            )

            rows = completed_result_rows(root, "job1")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "pg-w100-c100")
            self.assertEqual(rows[0]["terminals"], 100)
            self.assertEqual(rows[0]["tpmc"], 123.45)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "job1")

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("Finished Results: 1", text)
            self.assertIn("pg-w100-c100", text)
            self.assertIn("123.45", text)
            self.assertIn("456.78", text)
            self.assertNotIn("500", text)


if __name__ == "__main__":
    unittest.main()
