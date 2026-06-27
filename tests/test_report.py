from __future__ import annotations

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
            log_dir.mkdir(parents=True)
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
                            "status_path": str(run_dir / "status.json"),
                            "command_log_dir": str(log_dir),
                            "benchmark_result_dir": str(run_dir / "benchmark/result"),
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
            (log_dir / "runBenchmark.sh.stdout.log").write_text("ERROR: password authentication failed for user postgres\n", encoding="utf-8")

            report_path = generate_report(root, "campaign")

            report = report_path.read_text(encoding="utf-8")
            self.assertEqual(report_path, campaign_dir / "report/report.md")
            self.assertIn("Manual parameter commands:", report)
            self.assertIn(str(manual_path), report)
            self.assertIn("password: ***", report)
            self.assertIn("w=100; c=100", report)
            self.assertIn("ERROR: password authentication failed", report)


if __name__ == "__main__":
    unittest.main()
