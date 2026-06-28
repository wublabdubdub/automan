from __future__ import annotations

from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.report import _run_result, find_job_dir, find_job_dirs


def show_completed_results(root: Path, job_id: str | None = None) -> int:
    rows = completed_result_rows(root, job_id)
    if job_id:
        print(f"Job: {job_id}")
        print(f"Finished Results: {len(rows)}")
        print()
    if not rows:
        print("No completed benchmark results found.")
        return 1
    _print_rows(rows, detailed=bool(job_id))
    return 0


def completed_result_rows(root: Path, job_id: str | None = None) -> list[dict[str, Any]]:
    job_dirs = [find_job_dir(root, job_id)] if job_id else find_job_dirs(root)
    rows: list[dict[str, Any]] = []
    for job_dir in [path for path in job_dirs if path is not None]:
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        job_name = str(plan.get("job_id") or job_dir.name)
        targets = _target_map(plan)
        for run in plan.get("runs", []) if isinstance(plan.get("runs", []), list) else []:
            result = _run_result(root, run)
            if result.get("status") != "success":
                continue
            if result.get("measured_tpmc") is None or result.get("measured_tpmtotal") is None:
                continue
            target = targets.get(str(result.get("target_id")), {})
            run_id = str(result.get("run_id"))
            rows.append(
                {
                    "id": stable_result_id(job_name, run_id),
                    "job": job_name,
                    "run": run_id,
                    "target": result.get("target_id"),
                    "db_host": target.get("db_host", "-"),
                    "warehouse": result.get("warehouse"),
                    "terminals": result.get("terminals"),
                    "run_mins": result.get("run_mins"),
                    "tpmc": result.get("measured_tpmc"),
                    "tpmtotal": result.get("measured_tpmtotal"),
                    "status": result.get("status"),
                    "session_start": result.get("session_start"),
                    "session_end": result.get("session_end"),
                    "result_dir": result.get("benchmark_result_dir"),
                }
            )
    rows.sort(key=lambda row: str(row.get("session_end") or ""), reverse=True)
    return rows


def stable_result_id(job_id: str, run_id: str) -> str:
    prefix = f"{job_id}-"
    if run_id.startswith(prefix):
        return run_id[len(prefix):]
    return run_id


def _target_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for target in plan.get("targets", []) if isinstance(plan.get("targets", []), list) else []:
        connection = target.get("connection", {}) if isinstance(target.get("connection", {}), dict) else {}
        targets[str(target.get("id", "-"))] = {
            "db_host": connection.get("db_host", "-"),
        }
    return targets


def _print_rows(rows: list[dict[str, Any]], detailed: bool) -> None:
    columns = (
        ["id", "job", "target", "db_host", "warehouse", "terminals", "run_mins", "tpmc", "tpmtotal", "status", "session_end"]
        if not detailed
        else [
            "id",
            "target",
            "db_host",
            "warehouse",
            "terminals",
            "run_mins",
            "tpmc",
            "tpmtotal",
            "status",
            "session_start",
            "session_end",
            "result_dir",
        ]
    )
    headers = {
        "id": "ID",
        "job": "Job",
        "run": "Run",
        "target": "Target",
        "db_host": "DB Host",
        "warehouse": "WH",
        "terminals": "Terminals",
        "run_mins": "Run Mins",
        "tpmc": "tpmC",
        "tpmtotal": "tpmTOTAL",
        "status": "Status",
        "session_start": "Start Time",
        "session_end": "End Time",
        "result_dir": "Result Dir",
    }
    formatted = [{key: _format_cell(row.get(key)) for key in columns} for row in rows]
    widths = {
        key: max(len(headers[key]), *(len(row[key]) for row in formatted))
        for key in columns
    }
    print("  ".join(headers[key].ljust(widths[key]) for key in columns))
    print("  ".join("-" * widths[key] for key in columns))
    for row in formatted:
        print("  ".join(row[key].ljust(widths[key]) for key in columns))


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
