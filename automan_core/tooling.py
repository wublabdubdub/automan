from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

import paramiko

from automan_core.ssh import CommandResult, SSHClient


@dataclass(frozen=True)
class RemoteExecutionHost:
    host: str
    port: int
    user: str
    password: str
    workdir: str


def build_benchmarksql_on_linux(root: Path, remote: RemoteExecutionHost) -> list[CommandResult]:
    remote_tool_dir = f"{remote.workdir.rstrip('/')}/tools/benchmarksql"
    results = [
        SSHClient(remote.host, remote.port, remote.user, remote.password).run(
            f"set -e; cd {remote_tool_dir}; ant",
            timeout=1200,
        )
    ]
    if results[-1].exit_code != 0:
        return results

    local_dist = root / "tools" / "benchmarksql" / "dist"
    if local_dist.exists():
        shutil.rmtree(local_dist)
    local_dist.mkdir(parents=True, exist_ok=True)
    _download_remote_dir(remote, f"{remote_tool_dir}/dist", local_dist)
    results.append(
        CommandResult(
            command=f"sftp download {remote.host}:{remote_tool_dir}/dist -> {local_dist}",
            exit_code=0,
            stdout=str(local_dist),
            stderr="",
        )
    )
    return results


def prompt_remote_execution_host(args) -> RemoteExecutionHost:
    password = args.password
    if password is None:
        password = getpass("Linux SSH password: ")
    return RemoteExecutionHost(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        workdir=args.remote_workdir,
    )


def _download_remote_dir(remote: RemoteExecutionHost, remote_dir: str, local_dir: Path) -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=remote.host,
            port=remote.port,
            username=remote.user,
            password=remote.password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = client.open_sftp()
        try:
            _download_dir(sftp, remote_dir, local_dir)
        finally:
            sftp.close()
    finally:
        client.close()


def _download_dir(sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in sftp.listdir_attr(remote_dir):
        remote_path = f"{remote_dir.rstrip('/')}/{item.filename}"
        local_path = local_dir / item.filename
        if _is_remote_dir(item):
            _download_dir(sftp, remote_path, local_path)
        else:
            sftp.get(remote_path, str(local_path))


def _is_remote_dir(item: paramiko.SFTPAttributes) -> bool:
    return bool(item.st_mode and stat.S_ISDIR(item.st_mode))
