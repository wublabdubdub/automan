from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.list_results import _plan_benchmark_type, _runs_for_listing, legacy_result_id, result_id_map, stable_result_id
from automan_core.report import find_job_dirs


CONFIRM_TOKEN = "DELETE"


@dataclass(frozen=True)
class DeleteRecord:
    requested_id: str
    job_dir: Path
    job_name: str
    run: dict[str, Any]
    run_id: str
    stable_id: str


def delete_results(
    root: Path,
    result_ids: list[str],
    force: bool = False,
    input_fn: Callable[[str], str] = input,
) -> int:
    root = root.resolve()
    ids = _unique_ids(result_ids)
    if not ids:
        _print_fail("no result id specified")
        return 1

    records: list[DeleteRecord] = []
    for result_id in ids:
        matches = _matching_records(root, result_id)
        if not matches:
            _print_fail(f"result id not found: {result_id}")
            print("[HINT] run ./automan list -t tpcc or ./automan list -t ts to find result IDs")
            return 1
        if len(matches) > 1:
            _print_fail(f"result id is ambiguous: {result_id}")
            for match in matches:
                print(f"[HINT] full run id: {match.run_id} job={match.job_name}")
            return 1
        records.append(matches[0])

    records = _unique_records(records)
    paths_by_record = [(record, _run_paths(root, record.run)) for record in records]

    print("Will delete result(s):")
    for record, paths in paths_by_record:
        print(f"- id: {record.stable_id}")
        print(f"  job: {record.job_name}")
        print(f"  run: {record.run_id}")
        for label, path in paths:
            print(f"  {label}: {path}")

    if not force:
        answer = input_fn(f"Type {CONFIRM_TOKEN} to confirm: ")
        if answer != CONFIRM_TOKEN:
            print("[FAIL] delete cancelled")
            return 1

    by_job: dict[Path, list[DeleteRecord]] = {}
    for record, paths in paths_by_record:
        for _, path in paths:
            _remove_path(path)
        by_job.setdefault(record.job_dir, []).append(record)
        print(f"[ OK ] deleted result: {record.stable_id}")

    for job_dir, job_records in by_job.items():
        _rewrite_job_after_delete(root, job_dir, job_records)
    return 0


def delete_job(
    root: Path,
    job_id: str,
    force: bool = False,
    input_fn: Callable[[str], str] = input,
) -> int:
    print("[WARN] delete_job() is deprecated; use delete_results() with result IDs")
    matches: list[str] = []
    job_dir = root.resolve() / "runs" / "jobs" / job_id
    plan_path = job_dir / "resolved-plan.yaml"
    if not plan_path.exists():
        _print_fail(f"job not found: {job_id}")
        return 1
    plan = load_yaml(plan_path)
    job_name = str(plan.get("job_id") or job_dir.name)
    for run in _runs(plan):
        run_id = str(run.get("run_id"))
        matches.append(stable_result_id(job_name, run_id))
    return delete_results(root, matches, force=force, input_fn=input_fn)


def _unique_ids(result_ids: list[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for result_id in result_ids:
        cleaned = str(result_id).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ids.append(cleaned)
    return ids


def _matching_records(root: Path, result_id: str) -> list[DeleteRecord]:
    matches: list[DeleteRecord] = []
    ids_by_run = result_id_map(root)
    for job_dir in find_job_dirs(root):
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        job_name = str(plan.get("job_id") or job_dir.name)
        benchmark = _plan_benchmark_type(plan)
        for run in _runs_for_listing(root, job_name, benchmark, plan, benchmark):
            run_id = str(run.get("run_id"))
            if not run_id or run_id == "None":
                continue
            stable_id = ids_by_run.get((job_name, run_id), stable_result_id(job_name, run_id))
            legacy_id = legacy_result_id(job_name, run_id)
            if result_id not in {stable_id, run_id, legacy_id}:
                continue
            matches.append(
                DeleteRecord(
                    requested_id=result_id,
                    job_dir=job_dir,
                    job_name=job_name,
                    run=run,
                    run_id=run_id,
                    stable_id=stable_id,
                )
            )
    return matches


def _unique_records(records: list[DeleteRecord]) -> list[DeleteRecord]:
    unique: list[DeleteRecord] = []
    seen: set[tuple[Path, str]] = set()
    for record in records:
        key = (record.job_dir, record.run_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _run_paths(root: Path, run: dict[str, Any]) -> list[tuple[str, Path]]:
    run_id = str(run.get("run_id"))
    return _unique_paths(
        [
            ("run_dir", _safe_path(root, run.get("run_dir") or root / "runs" / run_id)),
            ("work_dir", _safe_path(root, run.get("work_dir") or root / "work" / "tpcc" / "benchmarksql" / run_id)),
        ]
    )


def _rewrite_job_after_delete(root: Path, job_dir: Path, deleted_records: list[DeleteRecord]) -> None:
    if not job_dir.exists():
        return
    plan = load_yaml(job_dir / "resolved-plan.yaml")
    deleted_run_ids = {record.run_id for record in deleted_records}
    remaining_runs = [run for run in _runs(plan) if str(run.get("run_id")) not in deleted_run_ids]
    if not remaining_runs:
        _remove_path(job_dir)
        print(f"[ OK ] deleted job dir: {job_dir}")
        return

    plan["runs"] = remaining_runs
    write_yaml(job_dir / "resolved-plan.yaml", plan)
    if (job_dir / "job.yaml").exists():
        write_yaml(job_dir / "job.yaml", plan)
    _remove_path(job_dir / "report")
    _rewrite_job_state(root, job_dir, plan)


def _runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    runs = plan.get("runs", [])
    return runs if isinstance(runs, list) else []


def _unique_paths(paths: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for label, path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append((label, path))
    return unique


def _safe_path(root: Path, raw: Any) -> Path:
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"refusing to delete path outside automan root: {resolved}") from exc
    if resolved == root:
        raise ValueError("refusing to delete automan root")
    return resolved


def _rewrite_job_state(root: Path, job_dir: Path, plan: dict[str, Any]) -> None:
    job_state = _load_job_state(job_dir)
    runs = _runs(plan)
    statuses = {str(run.get("run_id")): _run_status(root, run) for run in runs}
    total = len(runs)
    success = sum(1 for status in statuses.values() if status.get("status") == "success")
    running = sum(1 for status in statuses.values() if status.get("status") == "running")
    failed = sum(1 for status in statuses.values() if status.get("status") == "failed")
    cancelled = sum(1 for status in statuses.values() if status.get("status") == "cancelled")
    pending = sum(1 for status in statuses.values() if status.get("status") not in {"success", "running", "failed", "cancelled"})
    finished = success + failed + cancelled

    job_state["job_id"] = str(plan.get("job_id") or job_dir.name)
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

    target_states: list[dict[str, Any]] = []
    for target_id in target_ids:
        target_runs = [run for run in runs if str(run.get("target_id")) == target_id]
        target_statuses = {str(run.get("run_id")): statuses[str(run.get("run_id"))] for run in target_runs}
        target_success = sum(1 for status in target_statuses.values() if status.get("status") == "success")
        target_running = [(run, target_statuses[str(run.get("run_id"))]) for run in target_runs if target_statuses[str(run.get("run_id"))].get("status") == "running"]
        target_failed = sum(1 for status in target_statuses.values() if status.get("status") == "failed")
        target_cancelled = sum(1 for status in target_statuses.values() if status.get("status") == "cancelled")
        target_pending = sum(1 for status in target_statuses.values() if status.get("status") not in {"success", "running", "failed", "cancelled"})
        target_state = dict(original_targets.get(target_id, {"target_id": target_id}))
        target_state.update(
            {
                "target_id": target_id,
                "status": _overall_status(len(target_runs), target_success, len(target_running), target_failed, target_pending),
                "current_run": str(target_running[0][0].get("run_id")) if target_running else None,
                "current_phase": target_running[0][1].get("phase") if target_running else None,
                "finished_runs": target_success + target_failed + target_cancelled,
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
    if running > 0 or pending > 0:
        return "running"
    if failed > 0:
        return "failed"
    if total == 0 or success == total:
        return "success"
    return "unknown"


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


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _print_fail(message: str) -> None:
    print(f"[FAIL] {message}")
