from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TYPE_CHECKING

from automan_core.models import CollectorConfig, Target
from automan_core.ssh import CommandResult, SSHClient

if TYPE_CHECKING:
    from automan_core.task_runner import TaskDefinition


PERF_REMEDIATION_HINTS = [
    "required: install perf and run as root, or grant CAP_PERFMON/CAP_SYS_ADMIN as appropriate",
    "check: cat /proc/sys/kernel/perf_event_paranoid",
    "check: cat /proc/sys/kernel/kptr_restrict",
    "example temporary fix: sysctl kernel.perf_event_paranoid=-1",
]


@dataclass(frozen=True)
class CheckResult:
    level: str
    text: str


LocalRunner = Callable[..., subprocess.CompletedProcess]
SSHRunner = Callable[[str, int], CommandResult]
SSHRunnerFactory = Callable[[Target], SSHRunner]


def check_task_readiness(
    root: Path,
    task: TaskDefinition,
    local_runner: LocalRunner = subprocess.run,
    ssh_runner_factory: SSHRunnerFactory | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for target in task.targets:
        results.extend(check_database_connectivity(root, target, local_runner=local_runner))
    results.extend(check_collector_readiness(root, task.targets, task.collectors, local_runner, ssh_runner_factory))
    return results


def check_database_connectivity(root: Path, target: Target, local_runner: LocalRunner = subprocess.run) -> list[CheckResult]:
    command = [
        "psql",
        "-h",
        target.connection.db_host,
        "-p",
        str(target.connection.db_port),
        "-U",
        target.connection.db_user,
        "-d",
        target.connection.db_name,
        "-tAc",
        "select 1;",
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = target.connection.db_password
    try:
        completed = local_runner(command, cwd=root, text=True, capture_output=True, env=env, check=False)
    except OSError as exc:
        return [CheckResult("FAIL", f"{target.id}: psql connectivity check could not start: {exc}")]
    if completed.returncode == 0 and str(completed.stdout).strip() == "1":
        return [CheckResult("OK", f"{target.id}: database connectivity ok")]
    error = (str(completed.stderr or completed.stdout or "psql connectivity check failed")).strip()
    return [CheckResult("FAIL", f"{target.id}: {error}")]


def check_collector_readiness(
    root: Path,
    targets: list[Target],
    collectors: CollectorConfig,
    local_runner: LocalRunner = subprocess.run,
    ssh_runner_factory: SSHRunnerFactory | None = None,
) -> list[CheckResult]:
    if not collectors.enabled:
        return [CheckResult("HINT", "collectors disabled; skipping collector check gate")]

    results: list[CheckResult] = []
    if _needs_role(collectors, "execution"):
        results.extend(_check_host_collectors("execution", "local", collectors, local_runner=local_runner, cwd=root))

    for target in targets:
        if not _needs_role(collectors, "database"):
            continue
        host_label = f"{target.id}@{target.connection.ssh_host or target.connection.db_host}"
        if _database_collector_is_local(target):
            results.extend(_check_host_collectors("database", host_label, collectors, local_runner=local_runner, cwd=root))
        else:
            runner = _ssh_runner(target, ssh_runner_factory)
            results.extend(_check_host_collectors("database", host_label, collectors, ssh_runner=runner))
    return results


def check_local_command(
    tool: str,
    label: str,
    local_runner: LocalRunner = subprocess.run,
    cwd: Path | None = None,
) -> CheckResult:
    try:
        completed = local_runner(["sh", "-lc", f"command -v {tool}"], cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return CheckResult("FAIL", f"{label}: required command check could not start for {tool}: {exc}")
    if completed.returncode == 0:
        return CheckResult("OK", f"{label}: command available: {tool}")
    error = _command_error(completed.stderr, completed.stdout, f"{tool} not found in PATH")
    return CheckResult("FAIL", f"{label}: required command missing: {tool}; {error}")


def check_ssh_command(tool: str, label: str, ssh_runner: SSHRunner) -> CheckResult:
    result = ssh_runner(f"command -v {tool}", 30)
    if result.exit_code == 0:
        return CheckResult("OK", f"{label}: command available: {tool}")
    error = _command_error(result.stderr, result.stdout, f"{tool} not found in PATH")
    return CheckResult("FAIL", f"{label}: required command missing: {tool}; {error}")


def check_local_perf_record(
    frequency: int,
    label: str,
    local_runner: LocalRunner = subprocess.run,
    cwd: Path | None = None,
) -> list[CheckResult]:
    try:
        completed = local_runner(["sh", "-lc", _perf_probe_command(frequency)], cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return _perf_failure(label, f"perf record probe could not start: {exc}")
    if completed.returncode == 0:
        return [CheckResult("OK", f"{label}: perf record permission ok")]
    error = _command_error(completed.stderr, completed.stdout, "perf record probe failed")
    return _perf_failure(label, error)


def check_ssh_perf_record(frequency: int, label: str, ssh_runner: SSHRunner) -> list[CheckResult]:
    result = ssh_runner(_perf_probe_command(frequency), 120)
    if result.exit_code == 0:
        return [CheckResult("OK", f"{label}: perf record permission ok")]
    error = _command_error(result.stderr, result.stdout, "perf record probe failed")
    return _perf_failure(label, error)


def _check_host_collectors(
    role: str,
    label: str,
    collectors: CollectorConfig,
    local_runner: LocalRunner | None = None,
    ssh_runner: SSHRunner | None = None,
    cwd: Path | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    if collectors.system.enabled and role in collectors.system.host_roles:
        results.extend(_check_tools(collectors.system.tools, label, local_runner, ssh_runner, cwd))
    if collectors.perf.enabled and role in collectors.perf.host_roles:
        perf_tool_results = _check_tools(["perf"], label, local_runner, ssh_runner, cwd)
        results.extend(perf_tool_results)
        if any(result.level == "FAIL" for result in perf_tool_results):
            results.extend(CheckResult("HINT", hint) for hint in PERF_REMEDIATION_HINTS)
        else:
            if ssh_runner is not None:
                results.extend(check_ssh_perf_record(collectors.perf.frequency, label, ssh_runner))
            elif local_runner is not None:
                results.extend(check_local_perf_record(collectors.perf.frequency, label, local_runner, cwd))
    return results


def _check_tools(
    tools: Iterable[str],
    label: str,
    local_runner: LocalRunner | None,
    ssh_runner: SSHRunner | None,
    cwd: Path | None,
) -> list[CheckResult]:
    results = []
    for tool in tools:
        if ssh_runner is not None:
            results.append(check_ssh_command(tool, label, ssh_runner))
        elif local_runner is not None:
            results.append(check_local_command(tool, label, local_runner, cwd))
    return results


def _needs_role(collectors: CollectorConfig, role: str) -> bool:
    return (
        collectors.enabled
        and ((collectors.system.enabled and role in collectors.system.host_roles) or (collectors.perf.enabled and role in collectors.perf.host_roles))
    )


def _database_collector_is_local(target: Target) -> bool:
    connection = target.connection
    database_host = connection.ssh_host or connection.db_host
    return database_host in {"", "localhost", "127.0.0.1", connection.execution_host}


def _ssh_runner(target: Target, factory: SSHRunnerFactory | None) -> SSHRunner:
    if factory is not None:
        return factory(target)
    client = SSHClient(
        host=target.connection.ssh_host or target.connection.db_host,
        port=target.connection.ssh_port,
        user=target.connection.ssh_user,
        password=target.connection.ssh_password,
    )
    return client.run


def _perf_probe_command(frequency: int) -> str:
    return "\n".join(
        [
            "tmpdir=$(mktemp -d /tmp/automan-perf-check.XXXXXX)",
            "trap 'rm -rf \"$tmpdir\"' EXIT",
            f'perf record -F {frequency} -a -g -o "$tmpdir/perf.data" -- sleep 0.2',
            'perf script -i "$tmpdir/perf.data" >/dev/null',
            'perf report --stdio -i "$tmpdir/perf.data" >/dev/null',
        ]
    )


def _perf_failure(label: str, error: str) -> list[CheckResult]:
    return [
        CheckResult("FAIL", f"{label}: perf record check failed: {error}"),
        *[CheckResult("HINT", hint) for hint in PERF_REMEDIATION_HINTS],
    ]


def _command_error(stderr: object, stdout: object, fallback: str) -> str:
    return str(stderr or stdout or fallback).strip()
