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

    def test_lists_published_run_result_before_parent_job_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job1"
            run1 = root / "runs/job1-pg-w100-c100"
            run2 = root / "runs/job1-pg-w100-c500"
            run1.mkdir(parents=True)
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
                            "benchmark_result_dir": str(run1 / "benchmark/result"),
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
            write_json(job_dir / "status.json", {"job_id": "job1", "status": "running"})
            write_json(
                run1 / "result.json",
                {
                    "run_id": "job1-pg-w100-c100",
                    "target_id": "pg",
                    "warehouse": 100,
                    "terminals": 100,
                    "run_mins": 15,
                    "status": "success",
                    "benchmark_result_dir": str(run1 / "benchmark/result"),
                    "measured_tpmc": 111.11,
                    "measured_tpmtotal": 222.22,
                    "session_start": "2026-06-29 10:00:00",
                    "session_end": "2026-06-29 10:15:00",
                },
            )
            write_json(run2 / "status.json", {"run_id": "job1-pg-w100-c500", "status": "running"})

            all_rows = completed_result_rows(root)
            job_rows = completed_result_rows(root, "job1")

            self.assertEqual(all_rows, job_rows)
            self.assertEqual(len(job_rows), 1)
            self.assertEqual(job_rows[0]["id"], "pg-w100-c100")
            self.assertEqual(job_rows[0]["terminals"], 100)
            self.assertEqual(job_rows[0]["tpmc"], 111.11)
            self.assertEqual(job_rows[0]["tpmtotal"], 222.22)

    def test_ignores_published_results_without_complete_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job1"
            run1 = root / "runs/job1-pg-w100-c100"
            run1.mkdir(parents=True)
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
                        },
                    ],
                },
            )
            write_json(
                run1 / "result.json",
                {
                    "run_id": "job1-pg-w100-c100",
                    "target_id": "pg",
                    "status": "success",
                    "measured_tpmc": 111.11,
                    "measured_tpmtotal": None,
                },
            )

            rows = completed_result_rows(root, "job1")

            self.assertEqual(rows, [])

    def test_list_defaults_to_tpcc_columns_when_ts_results_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tpcc_job = root / "runs/jobs/tpcc-job"
            ts_job = root / "runs/jobs/ts-job"
            tpcc_run = root / "runs/tpcc-job-pg-w100-c100"
            ts_run = root / "runs/ts-job-ymatrix_mars3-ct1200-ts-write"
            tpcc_run.mkdir(parents=True)
            ts_run.mkdir(parents=True)
            write_yaml(
                tpcc_job / "resolved-plan.yaml",
                {
                    "job_id": "tpcc-job",
                    "benchmark": "tpcc",
                    "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
                    "runs": [
                        {
                            "run_id": "tpcc-job-pg-w100-c100",
                            "target_id": "pg",
                            "warehouse": 100,
                            "terminals": 100,
                            "run_mins": 15,
                            "run_dir": str(tpcc_run),
                        },
                    ],
                },
            )
            write_yaml(
                ts_job / "resolved-plan.yaml",
                {
                    "job_id": "ts-job",
                    "benchmark": "ts",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "127.0.0.2"}}],
                    "runs": [
                        {
                            "run_id": "ts-job-ymatrix_mars3-ct1200-ts-write",
                            "target_id": "ymatrix_mars3",
                            "stage": "ts-write",
                            "run_dir": str(ts_run),
                        },
                    ],
                },
            )
            write_json(
                tpcc_run / "result.json",
                {
                    "run_id": "tpcc-job-pg-w100-c100",
                    "target_id": "pg",
                    "warehouse": 100,
                    "terminals": 100,
                    "run_mins": 15,
                    "status": "success",
                    "measured_tpmc": 111.11,
                    "measured_tpmtotal": 222.22,
                    "session_end": "2026-06-29 10:15:00",
                },
            )
            write_json(
                ts_run / "result.json",
                {
                    "run_id": "ts-job-ymatrix_mars3-ct1200-ts-write",
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-write",
                    "status": "success",
                    "target_table": "dwd_vehicle",
                    "compress_threshold": 1200,
                    "producer_actual_qps": 90.0,
                    "written_rows": 3000,
                    "duration_seconds": 30,
                    "final_lag": 0,
                    "max_lag": 5,
                    "session_end": "2026-06-29 11:00:00",
                },
            )

            rows = completed_result_rows(root)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root)

            self.assertEqual(exit_code, 0)
            self.assertEqual([row["benchmark"] for row in rows], ["tpcc"])
            text = output.getvalue()
            self.assertIn("tpmC", text)
            self.assertIn("tpmTOTAL", text)
            self.assertIn("pg-w100-c100", text)
            self.assertNotIn("ymatrix_mars3-ct1200-ts-write", text)
            self.assertNotIn("Written Rows", text)

    def test_list_ts_type_uses_ts_columns_without_tpcc_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tpcc_job = root / "runs/jobs/tpcc-job"
            ts_job = root / "runs/jobs/ts-job"
            tpcc_run = root / "runs/tpcc-job-pg-w100-c100"
            ts_run = root / "runs/ts-job-ymatrix_mars3-ct1200-ts-write"
            tpcc_run.mkdir(parents=True)
            ts_run.mkdir(parents=True)
            write_yaml(
                tpcc_job / "resolved-plan.yaml",
                {
                    "job_id": "tpcc-job",
                    "benchmark": "tpcc",
                    "targets": [{"id": "pg", "connection": {"db_host": "127.0.0.1"}}],
                    "runs": [
                        {
                            "run_id": "tpcc-job-pg-w100-c100",
                            "target_id": "pg",
                            "warehouse": 100,
                            "terminals": 100,
                            "run_mins": 15,
                            "run_dir": str(tpcc_run),
                        },
                    ],
                },
            )
            write_yaml(
                ts_job / "resolved-plan.yaml",
                {
                    "job_id": "ts-job",
                    "benchmark": "ts",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "127.0.0.2"}}],
                    "runs": [
                        {
                            "run_id": "ts-job-ymatrix_mars3-ct1200-ts-write",
                            "target_id": "ymatrix_mars3",
                            "stage": "ts-write",
                            "run_dir": str(ts_run),
                        },
                    ],
                },
            )
            write_json(
                tpcc_run / "result.json",
                {
                    "run_id": "tpcc-job-pg-w100-c100",
                    "target_id": "pg",
                    "warehouse": 100,
                    "terminals": 100,
                    "run_mins": 15,
                    "status": "success",
                    "measured_tpmc": 111.11,
                    "measured_tpmtotal": 222.22,
                    "session_end": "2026-06-29 10:15:00",
                },
            )
            write_json(
                ts_run / "result.json",
                {
                    "run_id": "ts-job-ymatrix_mars3-ct1200-ts-write",
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-write",
                    "status": "success",
                    "target_table": "dwd_vehicle",
                    "compress_threshold": 1200,
                    "written_rows": 3000,
                    "duration_seconds": 30,
                    "session_end": "2026-06-29 11:00:00",
                },
            )

            rows = completed_result_rows(root, benchmark_type="ts")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, benchmark_type="ts")

            self.assertEqual(exit_code, 0)
            self.assertEqual([row["benchmark"] for row in rows], ["ts"])
            text = output.getvalue()
            self.assertIn("Rows", text)
            self.assertIn("Lag", text)
            self.assertIn("MaxLag", text)
            self.assertIn("ymatrix_mars3-ct1200-ts-write", text)
            self.assertNotIn("Producer Actual QPS", text)
            self.assertNotIn("Written Rows", text)
            self.assertNotIn("Final Lag", text)
            self.assertNotIn("Max Lag", text)
            self.assertNotIn("Job", text)
            self.assertNotIn("Target", text)
            self.assertNotIn("Table", text)
            self.assertNotIn("End Time", text)
            self.assertNotIn("pg-w100-c100", text)
            self.assertNotIn("tpmC", text)

    def test_ts_job_list_discovers_all_stage_runs_after_stage_plan_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/ts-job"
            write_run = root / "runs/ts-job-ymatrix_mars3-ct1200-ts-write"
            query_run = root / "runs/ts-job-ymatrix_mars3-ct1200-ts-query"
            point_run = root / "runs/ts-job-ymatrix_mars3-ct1200-point-query"
            for run in [write_run, query_run, point_run]:
                run.mkdir(parents=True)
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "ts-job",
                    "benchmark": "ts",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "127.0.0.2"}}],
                    "runs": [
                        {
                            "run_id": point_run.name,
                            "target_id": "ymatrix_mars3",
                            "stage": "point-query",
                            "run_dir": str(point_run),
                        },
                    ],
                },
            )
            write_json(
                write_run / "result.json",
                {
                    "run_id": write_run.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-write",
                    "status": "success",
                    "target_table": "iot_vehicle_raw_ct1200",
                    "compress_threshold": 1200,
                    "table_data_size": "289 MB",
                    "mxgate_write_start": "2026-06-29T17:47:45.064",
                    "mxgate_write_end": "2026-06-29T17:50:25.901",
                    "mxgate_elapsed_seconds": 160.836982,
                    "duration_seconds": 180,
                    "pressure_level": "high",
                    "produced_messages": 36000000,
                    "actual_qps": 223829.12,
                    "written_rows": 36000000,
                    "final_lag": 0,
                    "max_lag": 9399237,
                },
            )
            write_json(
                query_run / "result.json",
                {
                    "run_id": query_run.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-query",
                    "status": "success",
                    "target_table": "iot_vehicle_raw_ct1200",
                    "compress_threshold": 1200,
                    "table_data_size": "289 MB",
                    "rounds": 3,
                    "query_count": 9,
                    "avg_ms": 80.1,
                    "p50_ms": 79.9,
                    "p95_ms": 85.0,
                    "p99_ms": 86.0,
                    "rows_returned": 9,
                    "errors": 0,
                    "session_end": "2026-06-29T17:55:03",
                },
            )
            write_json(
                point_run / "result.json",
                {
                    "run_id": point_run.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "point-query",
                    "status": "success",
                    "target_table": "iot_vehicle_raw_ct1200",
                    "compress_threshold": 1200,
                    "table_data_size": "253 MB",
                    "rounds": 3,
                    "sample_size": 1000,
                    "query_count": 3000,
                    "avg_ms": 88.8,
                    "p50_ms": 88.2,
                    "p95_ms": 101.3,
                    "p99_ms": 107.8,
                    "hit_rate": 1.0,
                    "errors": 0,
                    "session_end": "2026-06-29T18:01:21",
                },
            )

            rows = completed_result_rows(root, "ts-job", benchmark_type="ts")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "ts-job", benchmark_type="ts")

            self.assertEqual(exit_code, 0)
            self.assertEqual({row["stage"] for row in rows}, {"ts-write", "ts-query", "point-query"})
            text = output.getvalue()
            self.assertIn("Finished Results: 3", text)
            self.assertIn("Stage: ts-write", text)
            self.assertIn("Actual QPS", text)
            self.assertIn("Final Lag", text)
            self.assertIn("Stage: ts-query", text)
            self.assertIn("Rows Returned", text)
            self.assertIn("Stage: point-query", text)
            self.assertIn("Sample Size", text)
            self.assertIn("Hit Rate", text)


if __name__ == "__main__":
    unittest.main()
