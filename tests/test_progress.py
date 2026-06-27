from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from automan_core.progress import _print_progress


class ProgressTest(unittest.TestCase):
    def test_print_progress_includes_target_last_error(self) -> None:
        progress = {
            "campaign_id": "campaign",
            "status": "failed",
            "finished_runs": 0,
            "total_runs": 2,
            "running_runs": 0,
            "pending_runs": 2,
            "failed_runs": 0,
            "last_error": "campaign level failure",
            "targets": [
                {
                    "target_id": "postgresql_heap_single_node",
                    "database_host": "192.168.100.29",
                    "status": "failed",
                    "current_run": None,
                    "current_phase": "manual_parameter_review",
                    "finished_runs": 0,
                    "total_runs": 2,
                    "last_error": "sudo requires a password",
                }
            ],
        }

        out = io.StringIO()
        with redirect_stdout(out):
            _print_progress(progress)

        self.assertIn("manual_parameter_review", out.getvalue())
        self.assertIn("campaign level failure", out.getvalue())
        self.assertIn("sudo requires a password", out.getvalue())


if __name__ == "__main__":
    unittest.main()
