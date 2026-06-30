from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TYPE_CHECKING

import paramiko

from automan_core.models import CollectorConfig, Target
from automan_core.ssh import CommandResult, SSHClient
from automan_core.tpch import tpch_data_status, tpch_ddl_profile
from automan_core.tpch_backend import remote_backend_dir

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
SFTPFetcher = Callable[[Target, str, Path], CommandResult]


def check_task_readiness(
    root: Path,
    task: TaskDefinition,
    local_runner: LocalRunner = subprocess.run,
    ssh_runner_factory: SSHRunnerFactory | None = None,
    sftp_fetcher: SFTPFetcher | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    skip_local_db_check = task.benchmark == "tpch" and task.tpch_config is not None and task.tpch_config.backend.type == "ymatrix-tpch"
    if not skip_local_db_check:
        for target in task.targets:
            results.extend(check_database_connectivity(root, target, local_runner=local_runner))
    if task.benchmark == "ts" and task.ts_config is not None:
        for target in task.targets:
            results.extend(check_mxgate_readiness(target, task.ts_config.mxgate))
    if task.benchmark == "ap" and task.ap_config is not None:
        results.extend(check_ap_readiness(root, task.ap_config))
    if task.benchmark == "tpch" and task.tpch_config is not None:
        results.extend(check_tpch_readiness(root, task, ssh_runner_factory=ssh_runner_factory))
    results.extend(check_collector_readiness(root, task.targets, task.collectors, local_runner, ssh_runner_factory, sftp_fetcher))
    return results


def check_ap_readiness(root: Path, config) -> list[CheckResult]:
    query_dir = root / "benchmarks" / "ap" / "queries" / config.query.query_set
    if not query_dir.exists():
        return [CheckResult("FAIL", f"AP query directory not found: {query_dir}")]
    files = sorted(path for path in query_dir.glob("*.sql") if path.is_file())
    if not files:
        return [CheckResult("FAIL", f"AP query directory has no .sql files: {query_dir}")]
    return [CheckResult("OK", f"AP query set ready: {config.query.query_set} ({len(files)} SQL file(s))")]


def check_tpch_readiness(root: Path, task: TaskDefinition, ssh_runner_factory: SSHRunnerFactory | None = None) -> list[CheckResult]:
    config = task.tpch_config
    if config is None:
        return [CheckResult("FAIL", "TPC-H configuration is missing")]
    if config.backend.type == "ymatrix-tpch":
        return _check_ymatrix_tpch_backend(root, task, ssh_runner_factory)
    results: list[CheckResult] = []
    stages = set(config.stages)
    if "tpch-load" in stages:
        schema_root = _resolve_root_path(root, config.schema_dir)
        for target in task.targets:
            profile = tpch_ddl_profile(target)
            schema_file = schema_root / profile / "schema.sql"
            if schema_file.exists():
                results.append(CheckResult("OK", f"{target.id}: TPC-H schema ready: {schema_file}"))
            else:
                results.append(CheckResult("FAIL", f"{target.id}: TPC-H schema file not found: {schema_file}"))
        for scale_factor in config.scale_factors:
            results.extend(_check_tpch_data(root, config, scale_factor))
    if "tpch-query" in stages:
        query_dir = _resolve_root_path(root, config.query_dir) / config.query_set
        if not query_dir.exists():
            results.append(CheckResult("FAIL", f"TPC-H query directory not found: {query_dir}"))
        else:
            files = sorted(path for path in query_dir.glob("*.sql") if path.is_file())
            if files:
                results.append(CheckResult("OK", f"TPC-H query set ready: {config.query_set} ({len(files)} SQL file(s))"))
            else:
                results.append(CheckResult("FAIL", f"TPC-H query directory has no .sql files: {query_dir}"))
    return results


def _check_ymatrix_tpch_backend(root: Path, task: TaskDefinition, ssh_runner_factory: SSHRunnerFactory | None) -> list[CheckResult]:
    config = task.tpch_config
    if config is None:
        return [CheckResult("FAIL", "TPC-H configuration is missing")]
    results: list[CheckResult] = []
    source_dir = _resolve_root_path(root, config.backend.source_dir)
    if (source_dir / "tpch.sh").exists() and (source_dir / "rollout.sh").exists():
        results.append(CheckResult("OK", f"TPC-H YMatrix backend source ready: {source_dir}"))
    else:
        results.append(CheckResult("FAIL", f"TPC-H YMatrix backend source not found or incomplete: {source_dir}"))

    for target in task.targets:
        label = f"{target.id}@{target.connection.db_host}"
        runner = _tpch_backend_ssh_runner(target, ssh_runner_factory)
        results.extend(check_ssh_workspace(target.connection.remote_workdir, label, runner))
        for tool in ["psql", "make", "gcc", "ssh", "scp", "tar"]:
            results.append(check_ssh_command(tool, label, runner))
        results.append(_check_remote_psql(target, label, runner, "select 1;", "database connectivity ok"))
        if config.backend.database_type == "matrixdb" and config.backend.load_data_type == "mxgate":
            results.append(
                _check_remote_psql(
                    target,
                    label,
                    runner,
                    "select count(*) from gp_segment_configuration;",
                    "MatrixDB segment metadata query ok",
                )
            )
        for scale_factor in config.scale_factors:
            fake_run = _fake_tpch_run_for_check(root, target.id, scale_factor, config)
            results.append(CheckResult("HINT", f"{label}: backend will run on db_host in {remote_backend_dir(target, config.backend, fake_run)}"))
    return results


def _check_remote_psql(target: Target, label: str, runner: SSHRunner, sql: str, success: str) -> CheckResult:
    env = {
        "PGHOST": target.connection.db_host,
        "PGPORT": str(target.connection.db_port),
        "PGDATABASE": target.connection.db_name,
        "PGUSER": target.connection.db_user,
        "PGPASSWORD": target.connection.db_password,
    }
    exports = " ".join(f"{name}={sh_quote(value)}" for name, value in env.items())
    command = f"{exports} psql -v ON_ERROR_STOP=1 -q -A -t -c {sh_quote(sql)}"
    result = runner(command, 60)
    if result.exit_code == 0:
        return CheckResult("OK", f"{label}: {success}")
    error = _command_error(result.stderr, result.stdout, "remote psql check failed")
    return CheckResult("FAIL", f"{label}: {error}")


def _tpch_backend_ssh_runner(target: Target, factory: SSHRunnerFactory | None) -> SSHRunner:
    if factory is not None:
        return factory(target)
    client = SSHClient(
        host=target.connection.db_host,
        port=target.connection.ssh_port,
        user=target.connection.ssh_user,
        password=target.connection.ssh_password,
    )
    return client.run


def _fake_tpch_run_for_check(root: Path, target_id: str, scale_factor: int, config) -> object:
    from automan_core.models import TpchRunSpec

    threshold = config.compress_threshold[0] if config.compress_threshold else None
    return TpchRunSpec(
        run_id=f"check-{target_id}-sf{scale_factor}",
        target_id=target_id,
        stage="tpch-load",
        ddl_profile="ym-mars3",
        compress_threshold=threshold,
        scale_factor=scale_factor,
        query_streams=config.query_streams[0] if config.query_streams else 1,
        run_mins=config.run_mins[0] if config.run_mins else 0,
        run_dir=root / "runs" / ".check",
        benchmark_dir=root / "runs" / ".check" / "benchmark",
        database_dir=root / "runs" / ".check" / "database",
        logs_dir=root / "runs" / ".check" / "logs",
        collector_dir=root / "runs" / ".check" / "collectors",
    )


def _tpch_data_dir(root: Path, template: str, scale_factor: int) -> Path:
    return _resolve_root_path(root, template.format(scale_factor=scale_factor))


def _check_tpch_data(root: Path, config, scale_factor: int) -> list[CheckResult]:
    mode = config.data_prepare.mode.lower()
    if mode == "skip":
        return [CheckResult("HINT", f"TPC-H data check skipped for scale_factor={scale_factor}")]
    status = tpch_data_status(root, config, scale_factor)
    data_dir = status["data_dir"]
    if status["ready"]:
        return [CheckResult("OK", f"TPC-H data ready: scale_factor={scale_factor} {data_dir}")]
    if status["empty"]:
        return [CheckResult("FAIL", f"empty TPC-H data file(s) under {data_dir}: {', '.join(status['empty'])}")]
    if mode == "existing":
        return [CheckResult("FAIL", f"TPC-H data file(s) missing under {data_dir}: {', '.join(status['missing'])}")]
    if mode != "auto":
        return [CheckResult("FAIL", f"unsupported tpch.data_prepare.mode: {config.data_prepare.mode}")]
    source_dir = _resolve_root_path(root, config.data_prepare.source_dir)
    dbgen_path = _tpch_dbgen_path(source_dir, config.data_prepare.dbgen_command)
    if source_dir.exists() or dbgen_path.exists():
        return [
            CheckResult(
                "WARN",
                f"TPC-H data will be generated automatically for scale_factor={scale_factor}: {data_dir}",
            )
        ]
    return [
        CheckResult("FAIL", f"TPC-H dbgen source not found: {source_dir}"),
        CheckResult("HINT", f"provide {config.data_prepare.generator} source under {source_dir}, or set tpch.data_prepare.mode=existing with prepared data"),
    ]


def _tpch_dbgen_path(source_dir: Path, dbgen_command: str) -> Path:
    command = dbgen_command.strip()
    if command.startswith("./"):
        return source_dir / command[2:]
    path = Path(command)
    if path.is_absolute():
        return path
    return source_dir / command


def _resolve_root_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


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
    sftp_fetcher: SFTPFetcher | None = None,
) -> list[CheckResult]:
    if not collectors.enabled:
        return [CheckResult("HINT", "collectors disabled; skipping collector check gate")]

    results: list[CheckResult] = []
    if _needs_role(collectors, "execution"):
        results.extend(_check_host_collectors("execution", "local", collectors, local_runner=local_runner, cwd=root))
        results.extend(check_local_workspace(root / "runs", "execution local", local_runner=local_runner, cwd=root))

    for target in targets:
        if not _needs_role(collectors, "database"):
            continue
        host_label = f"{target.id}@{target.connection.ssh_host or target.connection.db_host}"
        if _database_collector_is_local(target):
            results.extend(_check_host_collectors("database", host_label, collectors, local_runner=local_runner, cwd=root))
            results.extend(check_local_workspace(root / "runs", host_label, local_runner=local_runner, cwd=root))
        else:
            runner = _ssh_runner(target, ssh_runner_factory)
            results.extend(_check_host_collectors("database", host_label, collectors, ssh_runner=runner))
            results.extend(check_ssh_workspace(target.connection.remote_workdir, host_label, runner))
            results.extend(check_ssh_fetch(target, host_label, runner, root, sftp_fetcher))
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


def check_local_workspace(
    path: Path,
    label: str,
    local_runner: LocalRunner = subprocess.run,
    cwd: Path | None = None,
) -> list[CheckResult]:
    command = (
        f"mkdir -p {sh_quote(str(path))} && "
        f"probe=$(mktemp {sh_quote(str(path / '.automan-collector-check.XXXXXX'))}) && "
        "printf automan > \"$probe\" && test -s \"$probe\" && rm -f \"$probe\""
    )
    try:
        completed = local_runner(["sh", "-lc", command], cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return [CheckResult("FAIL", f"{label}: collector workspace check could not start: {exc}")]
    if completed.returncode == 0:
        return [CheckResult("OK", f"{label}: collector workspace writable: {path}")]
    error = _command_error(completed.stderr, completed.stdout, "collector workspace is not writable")
    return [
        CheckResult("FAIL", f"{label}: collector workspace is not writable: {path}; {error}"),
        CheckResult("HINT", f"grant write permission for the benchmark user on {path}"),
    ]


def check_ssh_workspace(remote_workdir: str, label: str, ssh_runner: SSHRunner) -> list[CheckResult]:
    base = remote_workdir.rstrip("/") or "/tmp"
    probe_dir = f"{base}/.automan_collectors/.check"
    command = (
        f"mkdir -p {sh_quote(probe_dir)} && "
        f"probe=$(mktemp {sh_quote(probe_dir + '/probe.XXXXXX')}) && "
        "printf automan > \"$probe\" && test -s \"$probe\" && rm -f \"$probe\""
    )
    result = ssh_runner(command, 60)
    if result.exit_code == 0:
        return [CheckResult("OK", f"{label}: remote collector workspace writable: {probe_dir}")]
    error = _command_error(result.stderr, result.stdout, "remote collector workspace is not writable")
    return [
        CheckResult("FAIL", f"{label}: remote collector directory is not writable: {probe_dir}; {error}"),
        CheckResult("HINT", f"grant write permission for {label} on {probe_dir} or set config_workdir to a writable path"),
    ]


def check_ssh_fetch(
    target: Target,
    label: str,
    ssh_runner: SSHRunner,
    root: Path,
    sftp_fetcher: SFTPFetcher | None = None,
) -> list[CheckResult]:
    base = target.connection.remote_workdir.rstrip("/") or "/tmp"
    probe_dir = f"{base}/.automan_collectors/.check"
    probe_file = f"{probe_dir}/fetch-probe.txt"
    write = ssh_runner(f"mkdir -p {sh_quote(probe_dir)} && printf automan-fetch > {sh_quote(probe_file)}", 60)
    if write.exit_code != 0:
        error = _command_error(write.stderr, write.stdout, "could not create remote fetch probe")
        return [CheckResult("FAIL", f"{label}: remote SFTP fetch probe could not be created: {error}")]
    fetcher = sftp_fetcher or _default_sftp_fetcher
    with tempfile.TemporaryDirectory(dir=str(root) if root.exists() else None) as tmp:
        local_path = Path(tmp) / "fetch-probe.txt"
        result = fetcher(target, probe_file, local_path)
    ssh_runner(f"rm -f {sh_quote(probe_file)}", 30)
    if result.exit_code == 0:
        return [CheckResult("OK", f"{label}: remote SFTP fetch ok")]
    error = _command_error(result.stderr, result.stdout, "SFTP fetch failed")
    return [
        CheckResult("FAIL", f"{label}: remote SFTP fetch unavailable: {error}"),
        CheckResult("HINT", "ensure SSH credentials allow SFTP subsystem and file download from config_workdir"),
    ]


def check_mxgate_readiness(target: Target, mxgate: object) -> list[CheckResult]:
    host = target.connection.db_host
    port = target.connection.ssh_port
    user = str(getattr(mxgate, "user", target.connection.ssh_user))
    password = str(getattr(mxgate, "password", target.connection.ssh_password))
    binary = str(getattr(mxgate, "binary", "mxgate"))
    workdir = str(getattr(mxgate, "workdir", "/home/mxadmin/automan"))
    label = f"mxgate@{host}"
    runner = SSHClient(host=host, port=port, user=user, password=password).run
    results = [check_ssh_command(binary, label, runner)]
    results.extend(check_ssh_workspace(workdir, label, runner))
    return results


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


def _default_sftp_fetcher(target: Target, remote_path: str, local_path: Path) -> CommandResult:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=target.connection.ssh_host or target.connection.db_host,
            port=target.connection.ssh_port,
            username=target.connection.ssh_user,
            password=target.connection.ssh_password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = client.open_sftp()
        try:
            sftp.get(remote_path, str(local_path))
        finally:
            sftp.close()
        if local_path.exists() and local_path.read_text(encoding="utf-8", errors="replace") == "automan-fetch":
            return CommandResult(f"sftp get {remote_path}", 0, str(local_path), "")
        return CommandResult(f"sftp get {remote_path}", 1, "", "downloaded probe content mismatch")
    except (OSError, paramiko.SSHException) as exc:
        return CommandResult(f"sftp get {remote_path}", 255, "", str(exc))
    finally:
        client.close()


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


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
