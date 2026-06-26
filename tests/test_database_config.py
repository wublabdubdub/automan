from __future__ import annotations

import unittest

from automan_core.database_config import _verify_params
from automan_core.models import ConnectionInfo
from automan_core.ssh import CommandResult


class FakeSSH:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        self.commands.append(command)
        return CommandResult(command=command, exit_code=0, stdout="128\n", stderr="")


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


if __name__ == "__main__":
    unittest.main()

