from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.report import find_job_dir, find_job_dirs


CONFIRM_TOKEN = "CLEAN"
ProcessExists = Callable[[str], bool]


def clean_stale_runs(
    root: Path,
    job_id: str | None = None,
    force: bool = False,
    process_exists: ProcessExists | None = None,
    input_fn: Callable[[str], str] = input,
) -> int:
    root = root.resolve()
    process_exists = process_exists or run_has_live_process
    selected = _select_jobs(root, job_id, process_exists)
    if selected["status"] != "ok":
        return _print_selection_failure(selected, job_id)

    jobs: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]] = selected["jobs"]
    cleanable_count = sum(len(items) for _, _, items in jobs)
    if cleanable_count == 0:
        print("[ OK ] no stale or failed runs found")
        return 0

    _print_clean_plan(root, jobs)
    if not force:
        print(f"Type {CONFIRM_TOKEN} to confirm: ", end="")
        answer = input_fn("")
        if answer != CONFIRM_TOKEN:
            print("[FAIL] clean cancelled")
            return 1

    for job_dir, plan, stale_runs in jobs:
        _clean_job(root, job_dir, plan, stale_runs)
    return 0


def run_has_live_process(run_id: str) -> bool:
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


def _select_jobs(root: Path, job_id: str | None, process_exists: ProcessExists) -> dict[str, Any]:
    if job_id:
        job_dir = find_job_dir(root, job_id)
        if job_dir is None:
            return {"status": "not_found"}
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        return {"status": "ok", "jobs": [(job_dir, plan, _stale_runs(root, job_dir, plan, process_exists))]}

    candidates: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]] = []
    for job_dir in find_job_dirs(root):
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        cleanable = _stale_runs(root, job_dir, plan, process_exists)
        if cleanable:
            candidates.append((job_dir, plan, cleanable))
    return {"status": "ok", "jobs": candidates}


def _print_selection_failure(selection: dict[str, Any], job_id: str | None) -> int:
    if selection["status"] == "not_found":
        print(f"[FAIL] job not found: {job_id}")
        return 1
    print("[FAIL] clean failed")
    return 1


def _stale_runs(root: Path, job_dir: Path, plan: dict[str, Any], process_exists: ProcessExists) -> list[dict[str, Any]]:
    job_state = _load_job_state(job_dir)
    current_runs = {
        str(target.get("current_run"))
        for target in job_state.get("targets", [])
        if isinstance(target, dict) and target.get("current_run")
    }
    stale: list[dict[str, Any]] = []
    for run in _runs(plan):
        run_id = str(run.get("run_id", ""))
        if not run_id:
            continue
        status = _run_status(root, run)
        if status.get("status") == "failed":
            stale.append(run)
            continue
        is_running = status.get("status") == "running" or run_id in current_runs
        if is_running and not process_exists(run_id):
            stale.append(run)
    return stale


def _print_clean_plan(root: Path, jobs: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]]) -> None:
    print("Will clean stale or failed run(s):")
    for job_dir, _, stale_runs in jobs:
        for run in stale_runs:
            run_id = str(run.get("run_id", "-"))
            status = _run_status(root, run)
            phase = status.get("phase") or "-"
            run_status = status.get("status") or "unknown"
            reason = "status=failed" if run_status == "failed" else "status=running no live process"
            print(f"[WARN] clean run: {run_id} status={run_status} phase={phase} reason={reason}")
            print(f"- job: {job_dir.name}")
            print(f"- run: {run_id}")
            print(f"- delete run_dir: {_safe_path(root, run.get('run_dir') or root / 'runs' / run_id)}")
            print(f"- delete work_dir: {_safe_path(root, run.get('work_dir') or root / 'work' / 'tpcc' / 'benchmarksql' / run_id)}")


def _clean_job(root: Path, job_dir: Path, plan: dict[str, Any], stale_runs: list[dict[str, Any]]) -> None:
    stale_ids = {str(run.get("run_id")) for run in stale_runs}
    for run in stale_runs:
        run_id = str(run.get("run_id"))
        run_dir = _safe_path(root, run.get("run_dir") or root / "runs" / run_id)
        work_dir = _safe_path(root, run.get("work_dir") or root / "work" / "tpcc" / "benchmarksql" / run_id)
        _remove_path(run_dir)
        print(f"[ OK ] deleted run dir: {run_dir}")
        _remove_path(work_dir)
        print(f"[ OK ] deleted work dir: {work_dir}")

    remaining_runs = [run for run in _runs(plan) if str(run.get("run_id")) not in stale_ids]
    if not remaining_runs:
        _remove_path(job_dir)
        print(f"[ OK ] deleted job dir: {job_dir}")
        return
    plan["runs"] = remaining_runs
    write_yaml(job_dir / "resolved-plan.yaml", plan)
    if (job_dir / "job.yaml").exists():
        write_yaml(job_dir / "job.yaml", plan)
    _rewrite_job_state(root, job_dir, plan)


def _rewrite_job_state(root: Path, job_dir: Path, plan: dict[str, Any]) -> None:
    job_state = _load_job_state(job_dir)
    runs = _runs(plan)
    statuses = {str(run.get("run_id")): _run_status(root, run) for run in runs}
    total = len(runs)
    success = sum(1 for status in statuses.values() if status.get("status") == "success")
    running = sum(1 for status in statuses.values() if status.get("status") == "running")
    failed = sum(1 for status in statuses.values() if status.get("status") == "failed")
    pending = sum(1 for status in statuses.values() if status.get("status") not in {"success", "running", "failed", "cancelled"})
    finished = success + failed + sum(1 for status in statuses.values() if status.get("status") == "cancelled")

    job_state["total_runs"] = total
    job_state["finished_runs"] = finished
    job_state["success_runs"] = success
    job_state["running_runs"] = running
    job_state["failed_runs"] = failed
    job_state["pending_runs"] = pending
    job_state["status"] = _overall_status(total, success, running, failed, pending)

    original_targets = {
        str(target.get("target_id")): dict(target)
        for target in job_state.get("targets", [])
        if isinstance(target, dict) and target.get("target_id")
    }
    target_ids = [str(target.get("id")) for target in plan.get("targets", []) if isinstance(target, dict)]
    for target_id in original_targets:
        if target_id not in target_ids:
            target_ids.append(target_id)
    target_states = []
    for target_id in target_ids:
        target_runs = [run for run in runs if str(run.get("target_id")) == target_id]
        target_statuses = {str(run.get("run_id")): statuses[str(run.get("run_id"))] for run in target_runs}
        target_running = [(run, target_statuses[str(run.get("run_id"))]) for run in target_runs if target_statuses[str(run.get("run_id"))].get("status") == "running"]
        target_success = sum(1 for status in target_statuses.values() if status.get("status") == "success")
        target_failed = sum(1 for status in target_statuses.values() if status.get("status") == "failed")
        target_pending = sum(1 for status in target_statuses.values() if status.get("status") not in {"success", "running", "failed", "cancelled"})
        current_run = str(target_running[0][0].get("run_id")) if target_running else None
        current_phase = target_running[0][1].get("phase") if target_running else None
        target_state = dict(original_targets.get(target_id, {"target_id": target_id}))
        target_state.update(
            {
                "target_id": target_id,
                "status": _overall_status(len(target_runs), target_success, len(target_running), target_failed, target_pending),
                "current_run": current_run,
                "current_phase": current_phase,
                "finished_runs": target_success + target_failed + sum(1 for status in target_statuses.values() if status.get("status") == "cancelled"),
                "total_runs": len(target_runs),
            }
        )
        if target_state["status"] == "success":
            target_state["last_error"] = None
        target_states.append(target_state)
    job_state["targets"] = target_states
    if job_state["status"] == "success":
        job_state["last_error"] = None
    write_json(job_dir / "job.json", job_state)
    status_data = load_yaml(job_dir / "status.json") if (job_dir / "status.json").exists() else {"job_id": job_dir.name}
    status_data["status"] = job_state["status"]
    if job_state["status"] == "success":
        status_data.pop("last_error", None)
    write_json(job_dir / "status.json", status_data)
    print(
        "[ OK ] updated job progress: "
        f"total={total} finished={finished} running={running} pending={pending} failed={failed} status={job_state['status']}"
    )


def _overall_status(total: int, success: int, running: int, failed: int, pending: int) -> str:
    if running > 0:
        return "running"
    if pending > 0:
        return "running"
    if failed > 0:
        return "failed"
    if total == 0 or success == total:
        return "success"
    return "unknown"


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    runs = plan.get("runs", [])
    return runs if isinstance(runs, list) else []


def _load_job_state(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    return load_yaml(path) if path.exists() else {}


def _run_status(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run.get("run_id", "-"))
    raw = run.get("status_path") or root / "runs" / run_id / "status.json"
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return {}
    loaded = load_yaml(path)
    return loaded if isinstance(loaded, dict) else {}


def _safe_path(root: Path, raw: Any) -> Path:
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"refusing to clean path outside automan root: {resolved}") from exc
    if resolved == root:
        raise ValueError("refusing to clean automan root")
    return resolved
