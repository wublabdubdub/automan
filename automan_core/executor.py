from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from automan_core.collectors import CollectorError, CollectorManager, NullCollectorManager
from automan_core.config import load_yaml, write_json
from automan_core.models import CollectorConfig, RunSpec, Target
from automan_core.ssh import CommandResult


FATAL_OUTPUT_PATTERNS = (
    re.compile(r"FATAL:"),
    re.compile(r"ERROR:"),
    re.compile(r"Exception"),
    re.compile(r"Failed to"),
    re.compile(r"password authentication failed", re.IGNORECASE),
)
COLLECTED_PHASES = {"runDatabaseBuild.sh", "runBenchmark.sh"}


def execute_campaign(
    root: Path,
    campaign_id: str,
    targets: list[Target],
    runs: list[RunSpec],
    collectors: CollectorConfig | dict | None = None,
) -> None:
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    _set_campaign_status(campaign_dir, "running")
    execution_host_check = _preflight_execution_host(targets)
    if execution_host_check.exit_code != 0:
        _append_timeline(campaign_dir, {"event": "preflight_failed", "results": [_result_dict(execution_host_check)]})
        _set_campaign_status(campaign_dir, "failed")
        return
    preflight = _preflight_benchmarksql(root)
    if preflight.exit_code != 0:
        _append_timeline(campaign_dir, {"event": "preflight_failed", "results": [_result_dict(preflight)]})
        _set_campaign_status(campaign_dir, "failed")
        return

    for target in targets:
        if target.manual_parameter_commands:
            _append_timeline(
                campaign_dir,
                {
                    "event": "manual_parameter_commands_declared",
                    "target": target.id,
                    "message": "database parameter changes are manual-only and were not executed by automan",
                },
            )
    if not runs:
        _set_campaign_status(campaign_dir, "success")
        return

    runs_by_host: dict[str, list[RunSpec]] = {}
    target_by_id = {target.id: target for target in targets}
    for run in runs:
        host = _scheduling_host(target_by_id[run.target_id])
        runs_by_host.setdefault(host, []).append(run)

    had_exception = False
    with ThreadPoolExecutor(max_workers=len(runs_by_host)) as pool:
        futures = [
            pool.submit(_execute_host_queue, root, campaign_id, host_runs, target_by_id, collectors)
            for host_runs in runs_by_host.values()
        ]
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                had_exception = True
                _append_timeline(campaign_dir, {"event": "host_queue_exception", "error": str(exc)})

    progress = load_yaml(campaign_dir / "progress.json")
    if had_exception or int(progress.get("failed_runs", 0)) > 0:
        _set_campaign_status(campaign_dir, "failed")
    else:
        _set_campaign_status(campaign_dir, "success")


def _execute_host_queue(
    root: Path,
    campaign_id: str,
    runs: list[RunSpec],
    target_by_id: dict[str, Target],
    collectors: CollectorConfig | dict | None = None,
) -> None:
    for run in runs:
        target = target_by_id[run.target_id]
        _execute_run(root, campaign_id, target, run, collectors)


def _execute_run(
    root: Path,
    campaign_id: str,
    target: Target,
    run: RunSpec,
    collectors: CollectorConfig | dict | None = None,
) -> None:
    run_dir = root / "runs" / run.run_id
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    run_dir.mkdir(parents=True, exist_ok=True)
    benchmark_parent_dir = run_dir / "benchmark"
    benchmark_parent_dir.mkdir(parents=True, exist_ok=True)
    _set_run_status(run_dir, "running", "prepare")
    _update_progress(campaign_dir, run.target_id, "running", run.run_id, "prepare")

    _prepare_benchmark_run_dir(root, target, run)

    commands = [
        ("runDatabaseBuild.sh", ["./runDatabaseBuild.sh", str(run.properties_path)]),
        ("runBenchmark.sh", ["./runBenchmark.sh", str(run.properties_path)]),
    ]
    should_destroy = not run.skip_destroy
    if run.skip_destroy:
        probe = _probe_tpcc_objects(target)
        _write_command_result(run_dir, "schema_probe", probe)
        _append_timeline(campaign_dir, {"event": "schema_probe", "run_id": run.run_id, "target": run.target_id, "exit_code": probe.exit_code, "stdout": probe.stdout, "stderr": probe.stderr})
        if probe.exit_code != 0:
            last_error = _command_error_summary(probe)
            _set_run_status(run_dir, "failed", "schema_probe", last_error)
            _mark_run_finished(campaign_dir, run.target_id, failed=True, last_error=last_error)
            return
        should_destroy = _tpcc_objects_exist(probe)

    if should_destroy:
        commands.insert(0, ("runDatabaseDestroy.sh", ["./runDatabaseDestroy.sh", str(run.properties_path)]))
    else:
        _append_timeline(campaign_dir, {"event": "phase_skipped", "run_id": run.run_id, "target": run.target_id, "phase": "runDatabaseDestroy.sh", "reason": "first_run_empty_schema"})
    collector_manager = _collector_manager(root, target, run, collectors)
    for phase, command in commands:
        _set_run_status(run_dir, "running", phase)
        _update_progress(campaign_dir, run.target_id, "running", run.run_id, phase)
        collector_error = None
        if phase in COLLECTED_PHASES:
            try:
                collector_manager.start_phase(phase)
                _append_timeline(campaign_dir, {"event": "collector_start", "run_id": run.run_id, "target": run.target_id, "phase": phase})
            except CollectorError as exc:
                phase_error = f"{phase}: {exc}"
                _append_timeline(campaign_dir, {"event": "collector_failed", "run_id": run.run_id, "target": run.target_id, "phase": phase, "error": str(exc)})
                _set_run_status(run_dir, "failed", phase, phase_error)
                _mark_run_finished(campaign_dir, run.target_id, failed=True, last_error=phase_error)
                return
            try:
                result = _run_local(command, cwd=run.benchmark_run_dir, timeout=10800)
            except Exception as exc:
                result = CommandResult(" ".join(command), 1, "", str(exc))
            finally:
                try:
                    collector_manager.stop_phase(phase)
                    _append_timeline(campaign_dir, {"event": "collector_stop", "run_id": run.run_id, "target": run.target_id, "phase": phase})
                except CollectorError as exc:
                    collector_error = str(exc)
                    _append_timeline(campaign_dir, {"event": "collector_failed", "run_id": run.run_id, "target": run.target_id, "phase": phase, "error": collector_error})
        else:
            result = _run_local(command, cwd=run.benchmark_run_dir, timeout=10800)
        _write_command_result(run_dir, phase, result)
        _append_timeline(campaign_dir, {"event": "phase_done", "run_id": run.run_id, "target": run.target_id, "phase": phase, "exit_code": result.exit_code})
        phase_error = _combine_phase_errors(_phase_failure(result), collector_error)
        if phase_error:
            _set_run_status(run_dir, "failed", phase, phase_error)
            _mark_run_finished(campaign_dir, run.target_id, failed=True, last_error=phase_error)
            return

    _set_run_status(run_dir, "success", "report")
    _mark_run_finished(campaign_dir, run.target_id, failed=False)


def _prepare_benchmark_run_dir(root: Path, target: Target, run: RunSpec) -> None:
    source_tool_dir = root / "tools" / "benchmarksql"
    target_tool_dir = run.work_dir / "benchmarksql"
    if target_tool_dir.exists():
        shutil.rmtree(target_tool_dir)
    shutil.copytree(source_tool_dir, target_tool_dir, ignore=shutil.ignore_patterns(".git"))
    _install_ddl_profile(root, target, run)
    if target.profile.storage_engine == "mars3":
        _render_mars3_table_creates(run.benchmark_run_dir, target.mars3_options)


def _collector_manager(
    root: Path,
    target: Target,
    run: RunSpec,
    collectors: CollectorConfig | dict | None,
) -> CollectorManager | NullCollectorManager:
    config = _collector_config_dict(root, collectors)
    if not config.get("enabled", True):
        return NullCollectorManager()
    return CollectorManager(root, target, run, config=config)


def _collector_config_dict(root: Path, collectors: CollectorConfig | dict | None) -> dict:
    if isinstance(collectors, CollectorConfig):
        return {
            "enabled": collectors.enabled,
            "system": {
                "enabled": collectors.system.enabled,
                "interval_seconds": collectors.system.interval_seconds,
                "host_roles": collectors.system.host_roles,
                "tools": collectors.system.tools,
            },
            "perf": {
                "enabled": collectors.perf.enabled,
                "phases": collectors.perf.phases,
                "host_roles": collectors.perf.host_roles,
                "frequency": collectors.perf.frequency,
                "call_graph": collectors.perf.call_graph,
                "record_scope": collectors.perf.record_scope,
            },
        }
    if isinstance(collectors, dict):
        return collectors
    config_path = root / "configs" / "collectors" / "default.yaml"
    if not config_path.exists():
        return {"enabled": False}
    config = load_yaml(config_path).get("collectors", {})
    return config if isinstance(config, dict) else {"enabled": False}


def _preflight_benchmarksql(root: Path) -> CommandResult:
    tool_dir = root / "tools" / "benchmarksql"
    dist_dir = tool_dir / "dist"
    if not dist_dir.exists() or not any(dist_dir.glob("BenchmarkSQL-*.jar")):
        return CommandResult(
            command="preflight benchmarksql dist",
            exit_code=1,
            stdout="",
            stderr=(
                "BenchmarkSQL dist/ is missing. Run "
                "python -m automan_core tools build-benchmarksql --host 172.16.100.143 --user root "
                "from the source workspace, then sync the project to the execution host."
            ),
        )
    for script in ("runDatabaseDestroy.sh", "runDatabaseBuild.sh", "runBenchmark.sh"):
        script_path = tool_dir / "run" / script
        if not script_path.exists():
            return CommandResult(command=f"preflight {script}", exit_code=1, stdout="", stderr=f"missing script: {script_path}")
        if not os.access(script_path, os.X_OK):
            return CommandResult(command=f"preflight {script}", exit_code=1, stdout="", stderr=f"script is not executable: {script_path}")
    java = _run_local(["java", "-version"], cwd=root, timeout=30)
    if java.exit_code != 0:
        return CommandResult(command="preflight java -version", exit_code=java.exit_code, stdout=java.stdout, stderr=java.stderr)
    psql = _run_local(["psql", "--version"], cwd=root, timeout=30)
    if psql.exit_code != 0:
        return CommandResult(command="preflight psql --version", exit_code=psql.exit_code, stdout=psql.stdout, stderr=psql.stderr)
    return CommandResult(command="preflight benchmarksql dist", exit_code=0, stdout=str(dist_dir), stderr="")


def _scheduling_host(target: Target) -> str:
    return target.connection.db_host or target.connection.ssh_host


def _preflight_execution_host(targets: list[Target]) -> CommandResult:
    expected = {target.connection.execution_host for target in targets if target.connection.execution_host}
    if not expected:
        return CommandResult(command="preflight execution host", exit_code=0, stdout="", stderr="")
    local_names = _local_host_markers()
    missing = sorted(host for host in expected if host not in local_names and host not in {"localhost", "127.0.0.1"})
    if missing:
        return CommandResult(
            command="preflight execution host",
            exit_code=1,
            stdout=f"local={sorted(local_names)}",
            stderr=f"current process is not running on configured execution host(s): {', '.join(missing)}",
        )
    return CommandResult(command="preflight execution host", exit_code=0, stdout=f"local={sorted(local_names)}", stderr="")


def _local_host_markers() -> set[str]:
    markers = {"localhost", "127.0.0.1", socket.gethostname()}
    try:
        markers.add(socket.getfqdn())
        markers.add(socket.gethostbyname(socket.gethostname()))
    except socket.error:
        pass
    try:
        result = subprocess.run(["hostname", "-I"], text=True, capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            markers.update(part.strip() for part in result.stdout.split() if part.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {marker for marker in markers if marker}


def _probe_tpcc_objects(target: Target) -> CommandResult:
    query = (
        "select count(*) "
        "from pg_catalog.pg_class c "
        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = current_schema() "
        "and c.relname like 'bmsql_%';"
    )
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
        query,
    ]
    return _run_local(command, cwd=Path.cwd(), timeout=60, env={"PGPASSWORD": target.connection.db_password})


def _tpcc_objects_exist(result: CommandResult) -> bool:
    for line in reversed(result.stdout.splitlines()):
        stripped = line.strip()
        if stripped:
            return int(stripped) > 0
    return False


def _install_ddl_profile(root: Path, target: Target, run: RunSpec) -> None:
    ddl_dir = root / run.ddl_dir
    for dirname in ("sql.common", "sql.postgres"):
        src = ddl_dir / dirname
        dst = run.benchmark_run_dir / dirname
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)


def _render_mars3_table_creates(benchmark_run_dir: Path, options: dict) -> None:
    table_creates = benchmark_run_dir / "sql.common" / "tableCreates.sql"
    text = table_creates.read_text(encoding="utf-8")
    prefer_load_mode = options.get("prefer_load_mode", "single")
    rowstore_size = options.get("rowstore_size", 64)
    compresstype = options.get("compresstype", "zstd")
    compresslevel = options.get("compresslevel", 1)
    compress_threshold = options.get("compress_threshold", 1200)
    default_block = """WITH(
  mars3options='prefer_load_mode=single,rowstore_size=64',
  compresstype=zstd,
  compresslevel=1,
  compress_threshold=1200
)"""
    rendered_block = f"""WITH(
  mars3options='prefer_load_mode={prefer_load_mode},rowstore_size={rowstore_size}',
  compresstype={compresstype},
  compresslevel={compresslevel},
  compress_threshold={compress_threshold}
)"""
    if default_block not in text:
        raise ValueError(f"cannot find default MARS3 WITH block in {table_creates}")
    table_creates.write_text(text.replace(default_block, rendered_block), encoding="utf-8")


def _run_local(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> CommandResult:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=run_env,
        )
        return CommandResult(
            command=" ".join(command),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=" ".join(command),
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"command timed out after {timeout} seconds",
        )


def _phase_failure(result: CommandResult) -> str | None:
    if result.exit_code != 0:
        return _command_error_summary(result)
    fatal_line = _fatal_output_line(result)
    if fatal_line:
        return f"{result.command}: {fatal_line}"
    return None


def _combine_phase_errors(command_error: str | None, collector_error: str | None) -> str | None:
    if command_error and collector_error:
        return f"{command_error}; collector error: {collector_error}"
    if collector_error:
        return f"collector error: {collector_error}"
    return command_error


def _fatal_output_line(result: CommandResult) -> str | None:
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in FATAL_OUTPUT_PATTERNS):
            return stripped
    return None


def _command_error_summary(result: CommandResult) -> str:
    fatal_line = _fatal_output_line(result)
    if fatal_line:
        return f"{result.command}: {fatal_line}"
    for text in (result.stderr, result.stdout):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return f"{result.command}: {stripped}"
    return f"{result.command}: exit code {result.exit_code}"


def _set_campaign_status(campaign_dir: Path, status: str) -> None:
    data = load_yaml(campaign_dir / "status.json") if (campaign_dir / "status.json").exists() else {}
    data["status"] = status
    data["updated_at"] = datetime.now().isoformat()
    write_json(campaign_dir / "status.json", data)
    progress_path = campaign_dir / "progress.json"
    if progress_path.exists():
        progress = load_yaml(progress_path)
        progress["status"] = status
        write_json(progress_path, progress)


def _set_run_status(run_dir: Path, status: str, phase: str | None, last_error: str | None = None) -> None:
    data = {"run_id": run_dir.name, "status": status, "phase": phase, "updated_at": datetime.now().isoformat()}
    if last_error:
        data["last_error"] = last_error
    write_json(run_dir / "status.json", data)


def _update_progress(campaign_dir: Path, target_id: str, status: str, current_run: str | None, current_phase: str | None) -> None:
    progress = load_yaml(campaign_dir / "progress.json")
    for target in progress["targets"]:
        if target["target_id"] == target_id:
            target["status"] = status
            target["current_run"] = current_run
            target["current_phase"] = current_phase
    progress["running_runs"] = sum(1 for target in progress["targets"] if target.get("current_run"))
    write_json(campaign_dir / "progress.json", progress)


def _mark_run_finished(campaign_dir: Path, target_id: str, failed: bool, last_error: str | None = None) -> None:
    progress = load_yaml(campaign_dir / "progress.json")
    progress["finished_runs"] = int(progress.get("finished_runs", 0)) + 1
    progress["failed_runs"] = int(progress.get("failed_runs", 0)) + (1 if failed else 0)
    progress["success_runs"] = int(progress.get("success_runs", 0)) + (0 if failed else 1)
    progress["pending_runs"] = max(0, int(progress.get("pending_runs", 0)) - 1)
    if last_error:
        progress["last_error"] = last_error
    for target in progress["targets"]:
        if target["target_id"] == target_id:
            target["finished_runs"] = int(target.get("finished_runs", 0)) + 1
            if failed:
                target["status"] = "failed"
                if last_error:
                    target["last_error"] = last_error
            elif int(target.get("finished_runs", 0)) >= int(target.get("total_runs", 0)):
                target["status"] = "success"
            else:
                target["status"] = "running"
            target["current_run"] = None
            target["current_phase"] = None
    progress["running_runs"] = sum(1 for target in progress["targets"] if target.get("current_run"))
    write_json(campaign_dir / "progress.json", progress)


def _append_timeline(campaign_dir: Path, event: dict) -> None:
    event = {"time": datetime.now().isoformat(), **event}
    with (campaign_dir / "timeline.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _write_command_result(run_dir: Path, phase: str, result: CommandResult) -> None:
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{phase}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (logs / f"{phase}.stderr.log").write_text(result.stderr, encoding="utf-8")


def _result_dict(result: CommandResult) -> dict:
    return {"command": result.command, "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}
