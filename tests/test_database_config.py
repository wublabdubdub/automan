from __future__ import annotations

import unittest

from automan_core.database_config import _normalize_param_value, _verify_params
from automan_core.models import ConnectionInfo
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


if __name__ == "__main__":
    unittest.main()
