from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automan_core.config import write_json, write_yaml
from automan_core.report import generate_report


class ReportTest(unittest.TestCase):
    def test_generate_report_summarizes_archive_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign_dir = root / "runs/campaigns/campaign"
            run_dir = root / "runs/run1"
            log_dir = run_dir / "logs"
            result_dir = run_dir / "benchmark/result"
            system_dir = run_dir / "collectors/runBenchmark.sh/database/system"
            perf_dir = run_dir / "collectors/runBenchmark.sh/database/perf"
            log_dir.mkdir(parents=True)
            result_dir.mkdir(parents=True)
            system_dir.mkdir(parents=True)
            perf_dir.mkdir(parents=True)
            (root / "templates").mkdir()
            (root / "templates/report.md.j2").write_text((Path(__file__).resolve().parents[1] / "templates/report.md.j2").read_text(encoding="utf-8"), encoding="utf-8")
            manual_path = campaign_dir / "manual-parameter-commands.sh"
            write_yaml(
                campaign_dir / "resolved-plan.yaml",
                {
                    "campaign_id": "campaign",
                    "matrix": {"warehouses": [100], "terminals": [100], "load_workers": 8, "run_mins": 30},
                    "archive": {"manual_parameter_commands_path": str(manual_path)},
                    "targets": [
                        {
                            "id": "pg",
                            "display_name": "PostgreSQL",
                            "connection": {
                                "db_host": "127.0.0.1",
                                "db_port": 5432,
                                "db_name": "postgres",
                                "db_user": "postgres",
                                "db_password": "***",
                            },
                            "ddl_profile": "postgresql_heap_single_node",
                            "manual_parameter_commands_path": str(manual_path),
                        }
                    ],
                    "runs": [
                        {
                            "run_id": "run1",
                            "target_id": "pg",
                            "warehouse": 100,
                            "terminals": 100,
                            "run_mins": 30,
                            "run_dir": str(run_dir),
                            "status_path": str(run_dir / "status.json"),
                            "command_log_dir": str(log_dir),
                            "benchmark_result_dir": str(result_dir),
                        }
                    ],
                },
            )
            write_json(
                campaign_dir / "progress.json",
                {
                    "campaign_id": "campaign",
                    "status": "failed",
                    "last_error": "runBenchmark.sh: ERROR: password authentication failed",
                    "targets": [{"target_id": "pg", "last_error": "ERROR: password authentication failed"}],
                },
            )
            write_json(run_dir / "status.json", {"run_id": "run1", "status": "failed", "last_error": "ERROR: password authentication failed"})
            write_json(
                log_dir / "runBenchmark.sh.result.json",
                {
                    "phase": "runBenchmark.sh",
                    "command": "./runBenchmark.sh tpcc.properties",
                    "exit_code": 0,
                    "stdout_path": str(log_dir / "runBenchmark.sh.stdout.log"),
                    "stderr_path": str(log_dir / "runBenchmark.sh.stderr.log"),
                },
            )
            (log_dir / "runBenchmark.sh.stdout.log").write_text(
                "\n".join(
                    [
                        "01:58:09,082 [Thread-1] INFO   jTPCC : Term-00, Measured tpmC (NewOrders) = 179.55",
                        "01:58:09,082 [Thread-1] INFO   jTPCC : Term-00, Measured tpmTOTAL = 329.17",
                        "01:58:09,082 [Thread-1] INFO   jTPCC : Term-00, Session Start     = 2016-05-25 01:58:07",
                        "01:58:09,082 [Thread-1] INFO   jTPCC : Term-00, Session End       = 2016-05-25 01:58:09",
                        "ERROR: password authentication failed for user postgres",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (system_dir / "vmstat.log").write_text(
                "\n".join(
                    [
                        "procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----",
                        " r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st",
                        " 1  0      0 100000  20000 300000    0    0     1     2  100  200  1  2 97  0  0",
                        " 2  0      0  90000  21000 310000    0    0     3     4  110  220  3  4 93  0  0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (perf_dir / "perf.data").write_bytes(b"PERF")
            (perf_dir / "perf.script.txt").write_text("postgres 123 [000] 1.23: cycles: ffffffff81000000 cpu_startup_entry\n", encoding="utf-8")
            (perf_dir / "perf.report.txt").write_text("# Samples: 10  of event 'cycles'\n  20.00% postgres\n", encoding="utf-8")
            (run_dir / "collectors/runBenchmark.sh/database/manifest.json").write_text(
                json.dumps(
                    {
                        "phase": "runBenchmark.sh",
                        "role": "database",
                        "status": "success",
                        "collectors": {
                            "system": {"status": "success", "files": ["system/vmstat.log"]},
                            "perf": {"status": "success", "files": ["perf/perf.data", "perf/perf.script.txt", "perf/perf.report.txt"]},
                        },
                    }
                ),
                encoding="utf-8",
            )

            report_path = generate_report(root, "campaign")

            report = report_path.read_text(encoding="utf-8")
            agent_context = json.loads((campaign_dir / "report/agent-context.json").read_text(encoding="utf-8"))
            self.assertEqual(report_path, campaign_dir / "report/report.md")
            self.assertIn("## Campaign", report)
            self.assertIn("manual_parameter_commands:", report)
            self.assertIn(str(manual_path), report)
            self.assertIn("password: ***", report)
            self.assertIn("179.55", report)
            self.assertIn("329.17", report)
            self.assertIn("2016-05-25 01:58:07", report)
            self.assertIn("2016-05-25 01:58:09", report)
            self.assertIn(str(system_dir / "vmstat.log"), report)
            self.assertIn(str(perf_dir / "perf.data"), report)
            self.assertIn(str(perf_dir / "perf.script.txt"), report)
            self.assertIn(str(perf_dir / "perf.report.txt"), report)
            self.assertIn("collection status", report.lower())
            self.assertIn("runBenchmark.sh", report)
            self.assertIn("success", report)
            self.assertIn("ERROR: password authentication failed", report)
            self.assertEqual(agent_context["parsed_results"][0]["measured_tpmc"], 179.55)
            self.assertEqual(agent_context["parsed_results"][0]["measured_tpmtotal"], 329.17)
            self.assertEqual(agent_context["parsed_results"][0]["elapsed_seconds"], 2)
            self.assertEqual(agent_context["parsed_results"][0]["command_results"][0]["phase"], "runBenchmark.sh")
            self.assertEqual(agent_context["parsed_results"][0]["command_results"][0]["exit_code"], 0)
            self.assertEqual(agent_context["artifact_paths"]["resource"][0]["sample_count"], 2)
            self.assertTrue(any(path["path"] == str(perf_dir / "perf.data") for path in agent_context["artifact_paths"]["perf"]))
            self.assertEqual(
                agent_context["collection_status"]["by_run"]["run1"][0],
                {
                    "phase": "runBenchmark.sh",
                    "role": "database",
                    "status": "success",
                    "system_status": "success",
                    "perf_status": "success",
                    "manifest_path": str(run_dir / "collectors/runBenchmark.sh/database/manifest.json"),
                },
            )
            self.assertIn("plan", agent_context)
            self.assertIn("progress", agent_context)
            self.assertTrue(agent_context["failures"])


if __name__ == "__main__":
    unittest.main()
