from __future__ import annotations

import posixpath
import socket
from pathlib import Path

import paramiko

from automan_core.ssh import CommandResult


class RemoteClient:
    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    def run(self, command: str, timeout: int = 120) -> CommandResult:
        client = self._connect()
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return CommandResult(command=command, exit_code=code, stdout=out, stderr=err)
        except (paramiko.SSHException, socket.error, TimeoutError) as exc:
            return CommandResult(command=command, exit_code=255, stdout="", stderr=str(exc))
        finally:
            client.close()

    def upload_file(self, local_path: Path, remote_path: str) -> CommandResult:
        command = f"sftp put {local_path} {remote_path}"
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                self._mkdir_p(sftp, posixpath.dirname(remote_path))
                sftp.put(str(local_path), remote_path)
            finally:
                sftp.close()
            return CommandResult(command=command, exit_code=0, stdout=remote_path, stderr="")
        except (OSError, paramiko.SSHException) as exc:
            return CommandResult(command=command, exit_code=255, stdout="", stderr=str(exc))
        finally:
            client.close()

    def upload_dir(self, local_dir: Path, remote_dir: str) -> CommandResult:
        command = f"sftp put -r {local_dir} {remote_dir}"
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                self._mkdir_p(sftp, remote_dir)
                for path in sorted(local_dir.rglob("*")):
                    relative = path.relative_to(local_dir).as_posix()
                    remote_path = posixpath.join(remote_dir, relative)
                    if path.is_dir():
                        self._mkdir_p(sftp, remote_path)
                    else:
                        self._mkdir_p(sftp, posixpath.dirname(remote_path))
                        sftp.put(str(path), remote_path)
            finally:
                sftp.close()
            return CommandResult(command=command, exit_code=0, stdout=remote_dir, stderr="")
        except (OSError, paramiko.SSHException) as exc:
            return CommandResult(command=command, exit_code=255, stdout="", stderr=str(exc))
        finally:
            client.close()

    def download_dir(self, remote_dir: str, local_dir: Path) -> CommandResult:
        command = f"sftp get -r {remote_dir} {local_dir}"
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                local_dir.mkdir(parents=True, exist_ok=True)
                self._download_tree(sftp, remote_dir, local_dir)
            finally:
                sftp.close()
            return CommandResult(command=command, exit_code=0, stdout=str(local_dir), stderr="")
        except (OSError, paramiko.SSHException) as exc:
            return CommandResult(command=command, exit_code=255, stdout="", stderr=str(exc))
        finally:
            client.close()

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        use_password = bool(self.password)
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password or None,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            look_for_keys=not use_password,
            allow_agent=not use_password,
        )
        return client

    def _mkdir_p(self, sftp: paramiko.SFTPClient, remote_dir: str) -> None:
        if not remote_dir or remote_dir == "/":
            return
        parts = []
        current = remote_dir
        while current not in {"", "/"}:
            parts.append(current)
            current = posixpath.dirname(current)
        for directory in reversed(parts):
            try:
                sftp.stat(directory)
            except OSError:
                sftp.mkdir(directory)

    def _download_tree(self, sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path) -> None:
        for attr in sftp.listdir_attr(remote_dir):
            remote_path = posixpath.join(remote_dir, attr.filename)
            local_path = local_dir / attr.filename
            if str(attr.longname).startswith("d"):
                local_path.mkdir(parents=True, exist_ok=True)
                self._download_tree(sftp, remote_path, local_path)
            else:
                sftp.get(remote_path, str(local_path))
