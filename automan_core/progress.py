from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.list_results import stable_result_id
from automan_core.report import find_job_dir, find_job_dirs, latest_job_id


PHASE_ACTIONS = {
    "prepare": "prepare",
    "schema_probe": "probe",
    "runDatabaseDestroy.sh": "destroy",
    "runDatabaseBuild.sh": "load",
    "runBenchmark.sh": "test",
    "ts-write": "write",
    "ts-query": "query",
    "point-query": "point-query",
    "ap-query": "ap-query",
    "tpch-load": "tpch-load",
    "tpch-query": "tpch-query",
    "report": "report",
}


def show_progress(root: Path, job_id: str | None = None, watch_seconds: int | None = None) -> int:
    if watch_seconds is None:
        return _show_once(root, job_id)

    interval = max(1, int(watch_seconds))
    while True:
        _clear_screen()
        exit_code = _show_once(root, job_id)
        if exit_code != 0 or not _selected_job_is_running(root, job_id):
            return exit_code
        time.sleep(interval)


def _show_once(root: Path, job_id: str | None) -> int:
    selection = _select_job(root, job_id)
    if selection["status"] == "missing":
        print("[FAIL] no Automan job found under runs/jobs")
        return 1
    if selection["status"] == "ambiguous":
        print("[FAIL] multiple running TPC-C jobs found")
        for item in selection["jobs"]:
            print(f"[HINT] job: {item}")
        print("[HINT] rerun with ./automan progress --job <job_id>")
        return 1
    if selection["status"] == "not_found":
        print(f"[FAIL] job not found: {job_id}")
        return 1

    job_dir = selection["job_dir"]
    plan = load_yaml(job_dir / "resolved-plan.yaml")
    job_state = _load_job_state(job_dir)
    status = str(job_state.get("status") or _load_status(job_dir))
    job_name = str(plan.get("job_id") or job_dir.name)
    level = "FAIL" if status == "failed" else "OK"
    print(f"{_tag(level)} job: {job_name} status={status}")

    active_runs = _active_runs(root, plan, job_state)
    if active_runs:
        for active in active_runs:
            _print_active_run(root, job_dir, plan, active)
    elif status == "running":
        print("[WARN] run: none active status=running")
    else:
        print("[HINT] run: none active")

    _print_total(job_state, plan)
    if status == "failed":
        _print_failure_hint(root, plan, job_state)
    benchmark = str(plan.get("benchmark", "tpcc"))
    type_arg = "" if benchmark == "tpcc" else f" -t {benchmark}"
    print(f"[HINT] completed results: ./automan list{type_arg} --job {job_name}")
    return 0


def _select_job(root: Path, job_id: str | None) -> dict[str, Any]:
    if job_id:
        job_dir = find_job_dir(root, job_id)
        return {"status": "ok", "job_dir": job_dir} if job_dir else {"status": "not_found"}

    job_dirs = find_job_dirs(root)
    if not job_dirs:
        return {"status": "missing"}
    running = [path for path in job_dirs if _job_status(path) == "running" and _job_has_current_activity(root, path)]
    if len(running) == 1:
        return {"status": "ok", "job_dir": running[0]}
    if len(running) > 1:
        return {"status": "ambiguous", "jobs": [path.name for path in running]}
    latest = latest_job_id(root)
    return {"status": "ok", "job_dir": find_job_dir(root, latest)} if latest else {"status": "missing"}


def _selected_job_is_running(root: Path, job_id: str | None) -> bool:
    selection = _select_job(root, job_id)
    if selection.get("status") != "ok":
        return False
    return _job_status(selection["job_dir"]) == "running"


def _job_status(job_dir: Path) -> str:
    state = _load_job_state(job_dir)
    if state.get("status"):
        return str(state["status"])
    return _load_status(job_dir)


def _job_has_current_activity(root: Path, job_dir: Path) -> bool:
    try:
        plan = load_yaml(job_dir / "resolved-plan.yaml")
    except (OSError, ValueError):
        plan = {}
    job_state = _load_job_state(job_dir)
    for active in _active_runs(root, plan, job_state):
        run = active["run"]
        run_id = str(run.get("run_id", ""))
        if run_id and _run_has_live_process(run_id):
            return True
        status = _run_status(root, run)
        updated = _parse_datetime(status.get("updated_at"))
        if updated and (datetime.now() - updated).total_seconds() < 7200:
            return True
    return False


def _run_has_live_process(run_id: str) -> bool:
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid,args"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    current_pid = str(os.getpid())
    for line in completed.stdout.splitlines():
        if run_id in line and (not current_pid or not line.strip().startswith(current_pid)):
            return True
    return False


def _load_job_state(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    return load_yaml(path) if path.exists() else {}


def _load_status(job_dir: Path) -> str:
    path = job_dir / "status.json"
    if not path.exists():
        return "unknown"
    return str(load_yaml(path).get("status", "unknown"))


def _active_runs(root: Path, plan: dict[str, Any], job_state: dict[str, Any]) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for target in job_state.get("targets", []) if isinstance(job_state.get("targets", []), list) else []:
        run_id = target.get("current_run")
        if run_id:
            run = _run_by_id(plan, str(run_id))
            if run:
                active.append({"run": run, "target_state": target})
    if active:
        return active
    for run in _runs(plan):
        status = _run_status(root, run)
        if status.get("status") == "running":
            active.append({"run": run, "target_state": {}})
    return active


def _print_active_run(root: Path, job_dir: Path, plan: dict[str, Any], active: dict[str, Any]) -> None:
    run = active["run"]
    job_id = str(plan.get("job_id") or job_dir.name)
    run_id = str(run.get("run_id", "-"))
    target = _target(plan, str(run.get("target_id", "-")))
    connection = target.get("connection", {}) if isinstance(target.get("connection", {}), dict) else {}
    status = _run_status(root, run)
    phase = str(status.get("phase") or active.get("target_state", {}).get("current_phase") or "-")
    action = PHASE_ACTIONS.get(phase, phase)

    benchmark = str(plan.get("benchmark", "tpcc"))
    if benchmark == "ts" or run.get("stage") in {"ts-write", "ts-query", "point-query"}:
        print(
            "[ OK ] run: "
            f"{stable_result_id(job_id, run_id)} "
            f"stage={run.get('stage', '-')} "
            f"target={run.get('target_id', '-')} "
            f"host={connection.get('db_host', '-')} "
            f"table={run.get('target_table', '-')} "
            f"ct={run.get('compress_threshold', '-')}"
        )
    elif benchmark == "ap" or run.get("stage") == "ap-query":
        print(
            "[ OK ] run: "
            f"{stable_result_id(job_id, run_id)} "
            f"stage={run.get('stage', '-')} "
            f"target={run.get('target_id', '-')} "
            f"host={connection.get('db_host', '-')} "
            f"table={run.get('source_table', '-')} "
            f"ct={run.get('compress_threshold', '-')}"
        )
    elif benchmark == "tpch" or run.get("stage") in {"tpch-load", "tpch-query"}:
        print(
            "[ OK ] run: "
            f"{stable_result_id(job_id, run_id)} "
            f"stage={run.get('stage', '-')} "
            f"target={run.get('target_id', '-')} "
            f"host={connection.get('db_host', '-')} "
            f"ddl={run.get('ddl_profile', '-')} "
            f"ct={run.get('compress_threshold', '-')} "
            f"sf={run.get('scale_factor', '-')}"
        )
    else:
        print(
            "[ OK ] run: "
            f"{stable_result_id(job_id, run_id)} "
            f"target={run.get('target_id', '-')} "
            f"host={connection.get('db_host', '-')} "
            f"wh={run.get('warehouse', '-')} "
            f"terminals={run.get('terminals', '-')}"
        )
    timing = _phase_timing(root, job_dir, run, phase, status)
    fields = [f"phase: {phase}", f"action={action}", f"elapsed={_format_duration(timing.get('elapsed_seconds'))}"]
    if phase == "runBenchmark.sh":
        expected = _expected_seconds(run)
        elapsed = timing.get("elapsed_seconds")
        fields.append(f"expected={_format_duration(expected)}")
        fields.append(f"remain={_format_duration(max(0, expected - elapsed) if elapsed is not None else None)}")
        fields.append(f"pct={_format_percent(elapsed, expected)}")
    print("[ OK ] " + " ".join(fields))


def _print_total(job_state: dict[str, Any], plan: dict[str, Any]) -> None:
    total = int(job_state.get("total_runs", len(_runs(plan))))
    finished = int(job_state.get("finished_runs", 0))
    running = int(job_state.get("running_runs", 0))
    pending = int(job_state.get("pending_runs", max(0, total - finished - running)))
    failed = int(job_state.get("failed_runs", 0))
    print(f"[ OK ] total: {total} finished={finished} running={running} pending={pending} failed={failed}")


def _print_failure_hint(root: Path, plan: dict[str, Any], job_state: dict[str, Any]) -> None:
    error = job_state.get("last_error")
    if error:
        print(f"[FAIL] error: {error}")
    for run in _runs(plan):
        status = _run_status(root, run)
        if status.get("last_error"):
            print(f"[FAIL] run: {run.get('run_id')} phase={status.get('phase', '-')} error={status['last_error']}")
            log_dir = run.get("command_log_dir") or root / "runs" / str(run.get("run_id")) / "logs"
            print(f"[HINT] logs: {log_dir}")
            return


def _runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    runs = plan.get("runs", [])
    return runs if isinstance(runs, list) else []


def _run_by_id(plan: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    for run in _runs(plan):
        if str(run.get("run_id")) == run_id:
            return run
    return None


def _target(plan: dict[str, Any], target_id: str) -> dict[str, Any]:
    for target in plan.get("targets", []) if isinstance(plan.get("targets", []), list) else []:
        if str(target.get("id")) == target_id:
            return target
    return {}


def _run_status(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run.get("run_id", "-"))
    raw = run.get("status_path") or root / "runs" / run_id / "status.json"
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return {}
    return load_yaml(path)


def _phase_timing(root: Path, job_dir: Path, run: dict[str, Any], phase: str, status: dict[str, Any]) -> dict[str, Any]:
    started = _parse_datetime(status.get("phase_started_at"))
    if started is None:
        started = _timeline_phase_start(job_dir, str(run.get("run_id", "-")), phase)
    if started is None:
        return {"started_at": None, "elapsed_seconds": None}
    return {"started_at": started, "elapsed_seconds": max(0, int((datetime.now() - started).total_seconds()))}


def _timeline_phase_start(job_dir: Path, run_id: str, phase: str) -> datetime | None:
    path = job_dir / "timeline.jsonl"
    if not path.exists():
        return None
    matched: datetime | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if str(event.get("run_id")) != run_id:
            continue
        if event.get("phase") != phase:
            continue
        if event.get("event") not in {"phase_start", "collector_start", "schema_probe"}:
            continue
        parsed = _parse_datetime(event.get("time"))
        if parsed:
            matched = parsed
    return matched


def _expected_seconds(run: dict[str, Any]) -> int:
    try:
        return int(run.get("run_mins", 0)) * 60
    except (TypeError, ValueError):
        return 0


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_percent(elapsed: int | None, expected: int) -> str:
    if elapsed is None or expected <= 0:
        return "unknown"
    return f"{min(100.0, elapsed * 100.0 / expected):.1f}%"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _tag(level: str) -> str:
    return {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "HINT": "[HINT]"}.get(level, "[INFO]")
