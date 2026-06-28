from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from automan_core.config import load_yaml
from automan_core.report import find_job_dir


CONFIRM_TOKEN = "DELETE"


def delete_job(
    root: Path,
    job_id: str,
    force: bool = False,
    input_fn: Callable[[str], str] = input,
) -> int:
    root = root.resolve()
    job_dir = find_job_dir(root, job_id)
    if job_dir is None:
        _print_fail(f"job not found: {job_id}")
        print("[HINT] run ./automan progress, ./automan list, or ls runs/jobs to find the job id")
        return 1

    plan_path = job_dir / "resolved-plan.yaml"
    if not plan_path.exists():
        _print_fail(f"job is missing resolved-plan.yaml: {job_id}")
        print("[HINT] refusing to guess run/work paths; inspect the job directory before deleting manually")
        return 1

    plan = load_yaml(plan_path)
    run_paths = _job_run_paths(root, plan)
    artifact_paths = _job_artifact_paths(root, job_dir.name)
    paths = _unique_paths([*run_paths, *artifact_paths, ("job_dir", job_dir)])

    print(f"Will delete job: {job_dir.name}")
    print(f"- runs: {len(_runs(plan))}")
    for label, path in paths:
        print(f"- {label}: {path}")

    if not force:
        answer = input_fn(f"Type {CONFIRM_TOKEN} to confirm: ")
        if answer != CONFIRM_TOKEN:
            print("[FAIL] delete cancelled")
            return 1

    for _, path in paths:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    print(f"[ OK ] deleted job: {job_dir.name}")
    return 0


def _runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    runs = plan.get("runs", [])
    return runs if isinstance(runs, list) else []


def _job_run_paths(root: Path, plan: dict[str, Any]) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for run in _runs(plan):
        run_id = str(run.get("run_id"))
        if not run_id or run_id == "None":
            continue
        run_dir = _safe_path(root, run.get("run_dir") or root / "runs" / run_id)
        work_dir = _safe_path(root, run.get("work_dir") or root / "work" / "tpcc" / "benchmarksql" / run_id)
        paths.append(("run_dir", run_dir))
        paths.append(("work_dir", work_dir))
    return paths


def _job_artifact_paths(root: Path, job_name: str) -> list[tuple[str, Path]]:
    candidates = [
        ("collector_dir", root / "runs" / "collector" / job_name),
        ("archive_dir", root / "runs" / "archives" / job_name),
    ]
    return [(label, _safe_path(root, path)) for label, path in candidates]


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


def _print_fail(message: str) -> None:
    print(f"[FAIL] {message}")
