from __future__ import annotations

import unittest

from automan_core.database_config import _apply_postgresql, _apply_ymatrix, _normalize_param_value, _verify_params
from automan_core.models import ConnectionInfo, DatabaseProfile
from automan_core.ssh import CommandResult


class FakeSSH:
    def __init__(self, stdout: str = "128\n") -> None:
        self.commands: list[str] = []
        self.stdout = stdout

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        self.commands.append(command)
        return CommandResult(command=command, exit_code=0, stdout=self.stdout, stderr="")


class DatabaseConfigTest(unittest.TestCase):
    def test_verify_params_redacts_password_in_recorded_result(self) -> None:
        ssh = FakeSSH()
        connection = ConnectionInfo(
            ssh_host="db",
            ssh_port=22,
            ssh_user="root",
            ssh_password="ssh-secret",
            remote_workdir="/root/automan",
            db_host="db",
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_password="db-secret",
        )

        results = _verify_params(ssh, connection, {"max_connections": "128"})

        self.assertIn("PGPASSWORD=db-secret", ssh.commands[0])
        self.assertIn("PGPASSWORD=***", results[0].command)
        self.assertNotIn("db-secret", results[0].command)

    def test_verify_params_fails_when_actual_value_differs(self) -> None:
        ssh = FakeSSH(stdout="64\n")
        connection = ConnectionInfo(
            ssh_host="db",
            ssh_port=22,
            ssh_user="root",
            ssh_password="ssh-secret",
            remote_workdir="/root/automan",
            db_host="db",
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_password="db-secret",
        )

        results = _verify_params(ssh, connection, {"max_connections": "128"})

        self.assertEqual(results[0].exit_code, 2)
        self.assertIn("expected=128", results[0].stdout)
        self.assertIn("actual=64", results[0].stdout)

    def test_normalize_param_value_handles_common_database_units(self) -> None:
        self.assertEqual(_normalize_param_value("8192MB"), _normalize_param_value("8GB"))
        self.assertEqual(_normalize_param_value("120s"), _normalize_param_value("2min"))
        self.assertEqual(_normalize_param_value("on"), _normalize_param_value("true"))

    def test_postgresql_restart_is_logged_to_avoid_ssh_channel_hang(self) -> None:
        ssh = FakeSSH()
        connection = ConnectionInfo(
            ssh_host="db",
            ssh_port=22,
            ssh_user="postgres",
            ssh_password="ssh-secret",
            remote_workdir="/root/automan",
            db_host="db",
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_password="db-secret",
            postgresql_conf="/data/postgresql.conf",
            restart_command="pg_ctl restart -D /data",
        )

        _apply_postgresql(ssh, connection, {"max_connections": "128"})

        self.assertIn("mktemp /tmp/automan-postgresql_restart", ssh.commands[0])
        self.assertIn("pg_ctl restart -D /data", ssh.commands[0])
        self.assertIn('> "${_automan_log}" 2>&1', ssh.commands[0])

    def test_ymatrix_restart_is_logged_to_avoid_ssh_channel_hang(self) -> None:
        ssh = FakeSSH()
        connection = ConnectionInfo(
            ssh_host="db",
            ssh_port=22,
            ssh_user="mxadmin",
            ssh_password="ssh-secret",
            remote_workdir="/root/automan",
            db_host="db",
            db_port=5432,
            db_name="postgres",
            db_user="mxadmin",
            db_password="db-secret",
            restart_command="mxstop -afr",
        )
        profile = DatabaseProfile(
            id="ymatrix_heap_master_only",
            display_name="YMatrix heap master only",
            database_type="ymatrix",
            storage_engine="heap",
            test_mode="master_only",
            ddl_profile="ymatrix_heap_master_only",
            ddl_dir="benchmarks/tpcc/benchmarksql/ddl/ymatrix_heap_master_only",
            requires_ddl_confirmation=False,
        )

        _apply_ymatrix(ssh, connection, {"max_connections": "128"})

        self.assertIn("gpconfig -c max_connections -v 128", ssh.commands[0])
        self.assertIn("mktemp /tmp/automan-ymatrix_restart", ssh.commands[1])
        self.assertIn("mxstop -afr", ssh.commands[1])
        self.assertIn('> "${_automan_log}" 2>&1', ssh.commands[1])


if __name__ == "__main__":
    unittest.main()
