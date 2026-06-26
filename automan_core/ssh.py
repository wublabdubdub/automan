from __future__ import annotations

import socket
from dataclasses import dataclass

import paramiko


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


class SSHClient:
    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10,
                look_for_keys=False,
                allow_agent=False,
            )
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return CommandResult(command=command, exit_code=code, stdout=out, stderr=err)
        except (paramiko.SSHException, socket.error, TimeoutError) as exc:
            return CommandResult(command=command, exit_code=255, stdout="", stderr=str(exc))
        finally:
            client.close()

