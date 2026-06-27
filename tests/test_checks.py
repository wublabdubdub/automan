from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from automan_core.checks import check_collector_readiness
from automan_core.models import CollectorConfig, PerfCollectorConfig, SystemCollectorConfig


class ChecksTest(unittest.TestCase):
    def test_missing_perf_fails_with_remediation_hints(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=False),
            perf=PerfCollectorConfig(enabled=True, host_roles=["execution"], frequency=99),
        )

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(Path(tmp), [], collectors, local_runner=missing_perf_runner)

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results))
        self.assertIn("required command missing: perf", text)
        self.assertIn("required: install perf and run as root", text)
        self.assertIn("check: cat /proc/sys/kernel/perf_event_paranoid", text)
        self.assertIn("example temporary fix: sysctl kernel.perf_event_paranoid=-1", text)


def missing_perf_runner(command, **kwargs):
    return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="perf not found")


if __name__ == "__main__":
    unittest.main()
