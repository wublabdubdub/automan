from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from automan_core.checks import check_collector_readiness
from automan_core.models import CollectorConfig, ConnectionInfo, DatabaseProfile, PerfCollectorConfig, SystemCollectorConfig, Target
from automan_core.ssh import CommandResult


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

    def test_remote_collector_directory_unwritable_fails_with_clear_error(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=False),
            perf=PerfCollectorConfig(enabled=True, host_roles=["database"], frequency=99),
        )
        target = Target(_profile(), _remote_connection(), {}, {}, False, {})

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(
                Path(tmp),
                [target],
                collectors,
                ssh_runner_factory=lambda target: remote_unwritable_runner,
            )

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results), text)
        self.assertIn("remote collector directory is not writable", text)
        self.assertIn("/readonly/automan/.automan_collectors", text)

    def test_remote_sftp_fetch_unavailable_fails_before_run(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=True, host_roles=["database"], tools=["vmstat"]),
            perf=PerfCollectorConfig(enabled=False),
        )
        target = Target(_profile(), _remote_connection(), {}, {}, False, {})

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(
                Path(tmp),
                [target],
                collectors,
                ssh_runner_factory=lambda target: remote_ok_runner,
                sftp_fetcher=remote_sftp_failed,
            )

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results), text)
        self.assertIn("remote SFTP fetch unavailable", text)
        self.assertIn("SFTP subsystem disabled", text)


def missing_perf_runner(command, **kwargs):
    return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="perf not found")


def remote_unwritable_runner(command: str, timeout: int) -> CommandResult:
    if ".automan_collectors" in command or "test -w" in command:
        return CommandResult(command, 1, "", "Permission denied")
    return CommandResult(command, 0, "/usr/bin/perf\n", "")


def remote_ok_runner(command: str, timeout: int) -> CommandResult:
    return CommandResult(command, 0, "/usr/bin/tool\n", "")


def remote_sftp_failed(target: Target, remote_path: str, local_path: Path) -> CommandResult:
    return CommandResult(f"sftp get {remote_path}", 255, "", "SFTP subsystem disabled")


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="postgresql_heap_single_node",
        display_name="PostgreSQL heap single node",
        database_type="postgresql",
        storage_engine="heap",
        test_mode="single_node",
        ddl_profile="postgresql_heap_single_node",
        ddl_dir="benchmarks/tpcc/benchmarksql/ddl/postgresql_heap_single_node",
        requires_ddl_confirmation=False,
    )


def _remote_connection() -> ConnectionInfo:
    return ConnectionInfo(
        ssh_host="db.example.test",
        ssh_port=22,
        ssh_user="root",
        ssh_password="secret",
        remote_workdir="/readonly/automan",
        db_host="10.0.0.20",
        db_port=5432,
        db_name="postgres",
        db_user="postgres",
        db_password="secret",
        execution_host="bench.example.test",
    )


if __name__ == "__main__":
    unittest.main()


