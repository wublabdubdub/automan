from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from automan_core.config import load_yaml, write_json
from automan_core.database_config import apply_database_params
from automan_core.models import RunSpec, Target
from automan_core.ssh import SSHClient, CommandResult


def execute_campaign(root: Path, campaign_id: str, targets: list[Target], runs: list[RunSpec]) -> None:
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    _set_campaign_status(campaign_dir, "running")

    for target in targets:
        if target.apply_params:
            results = apply_database_params(target.profile, target.connection, target.accepted_params)
            _append_timeline(campaign_dir, {"event": "database_config_applied", "target": target.id, "results": [_result_dict(r) for r in results]})
            if any(result.exit_code != 0 for result in results):
                _set_campaign_status(campaign_dir, "failed")
                return

    runs_by_host: dict[str, list[RunSpec]] = {}
    target_by_id = {target.id: target for target in targets}
    for run in runs:
        host = target_by_id[run.target_id].connection.ssh_host
        runs_by_host.setdefault(host, []).append(run)

    with ThreadPoolExecutor(max_workers=len(runs_by_host)) as pool:
        futures = [
            pool.submit(_execute_host_queue, root, campaign_id, host_runs, target_by_id)
            for host_runs in runs_by_host.values()
        ]
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                _append_timeline(campaign_dir, {"event": "host_queue_exception", "error": str(exc)})

    progress = load_yaml(campaign_dir / "progress.json")
    if int(progress.get("failed_runs", 0)) > 0:
        _set_campaign_status(campaign_dir, "failed")
    else:
        _set_campaign_status(campaign_dir, "success")


def _execute_host_queue(root: Path, campaign_id: str, runs: list[RunSpec], target_by_id: dict[str, Target]) -> None:
    for run in runs:
        target = target_by_id[run.target_id]
        _execute_run(root, campaign_id, target, run)


def _execute_run(root: Path, campaign_id: str, target: Target, run: RunSpec) -> None:
    run_dir = root / "runs" / run.run_id
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _set_run_status(run_dir, "running", "prepare")
    _update_progress(campaign_dir, run.target_id, "running", run.run_id, "prepare")

    _install_ddl_profile(root, target, run)

    ssh = SSHClient(target.connection.ssh_host, target.connection.ssh_port, target.connection.ssh_user, target.connection.ssh_password)
    remote_run_dir = f"{target.connection.remote_workdir}/tools/benchmarksql/run"
    commands = [
        ("runDatabaseDestroy.sh", f"cd {remote_run_dir} && ./runDatabaseDestroy.sh {run.properties_path}"),
        ("runDatabaseBuild.sh", f"cd {remote_run_dir} && ./runDatabaseBuild.sh {run.properties_path}"),
        ("runBenchmark.sh", f"cd {remote_run_dir} && ./runBenchmark.sh {run.properties_path}"),
    ]
    for phase, command in commands:
        _set_run_status(run_dir, "running", phase)
        _update_progress(campaign_dir, run.target_id, "running", run.run_id, phase)
        result = ssh.run(command, timeout=10800)
        _write_command_result(run_dir, phase, result)
        _append_timeline(campaign_dir, {"event": "phase_done", "run_id": run.run_id, "target": run.target_id, "phase": phase, "exit_code": result.exit_code})
        if result.exit_code != 0:
            _set_run_status(run_dir, "failed", phase)
            _mark_run_finished(campaign_dir, run.target_id, failed=True)
            return

    _set_run_status(run_dir, "success", "report")
    _mark_run_finished(campaign_dir, run.target_id, failed=False)


def _install_ddl_profile(root: Path, target: Target, run: RunSpec) -> None:
    ddl_dir = root / run.ddl_dir
    tool_run_dir = root / "tools" / "benchmarksql" / "run"
    for dirname in ("sql.common", "sql.postgres"):
        src = ddl_dir / dirname
        dst = tool_run_dir / dirname
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)


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


def _set_run_status(run_dir: Path, status: str, phase: str | None) -> None:
    write_json(run_dir / "status.json", {"run_id": run_dir.name, "status": status, "phase": phase, "updated_at": datetime.now().isoformat()})


def _update_progress(campaign_dir: Path, target_id: str, status: str, current_run: str | None, current_phase: str | None) -> None:
    progress = load_yaml(campaign_dir / "progress.json")
    for target in progress["targets"]:
        if target["target_id"] == target_id:
            target["status"] = status
            target["current_run"] = current_run
            target["current_phase"] = current_phase
    running = sum(1 for target in progress["targets"] if target["status"] == "running")
    progress["running_runs"] = running
    write_json(campaign_dir / "progress.json", progress)


def _mark_run_finished(campaign_dir: Path, target_id: str, failed: bool) -> None:
    progress = load_yaml(campaign_dir / "progress.json")
    progress["finished_runs"] = int(progress.get("finished_runs", 0)) + (0 if failed else 1)
    progress["failed_runs"] = int(progress.get("failed_runs", 0)) + (1 if failed else 0)
    progress["pending_runs"] = max(0, int(progress.get("pending_runs", 0)) - 1)
    for target in progress["targets"]:
        if target["target_id"] == target_id:
            target["finished_runs"] = int(target.get("finished_runs", 0)) + (0 if failed else 1)
            target["status"] = "failed" if failed else "running"
            target["current_run"] = None
            target["current_phase"] = None
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
