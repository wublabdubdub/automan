from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from automan_core.config import load_yaml, write_json
from automan_core.models import Target, TsRunSpec
from automan_core.result_summary import load_published_run_result
from automan_core.report import _run_result, find_job_dir, find_job_dirs
from automan_core.ssh import CommandResult
from automan_core.task_runner import load_task_definition
from automan_core.ts import _run_local, _table_data_size

RESULT_ID_MIN_LENGTH = 10
RESULT_ID_MAX_LENGTH = 40


Runner = Callable[[List[str], Path, int, Optional[Dict[str, str]]], CommandResult]


def show_completed_results(
    root: Path,
    job_id: str | None = None,
    benchmark_type: str = "tpcc",
    refresh_size: bool = False,
    inventory_path: Path | None = None,
) -> int:
    try:
        rows = completed_result_rows(root, job_id, benchmark_type, refresh_size=refresh_size, inventory_path=inventory_path)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    if job_id:
        print(f"Job: {job_id}")
        print(f"Finished Results: {len(rows)}")
        print()
    if not rows:
        print("No completed benchmark results found.")
        return 1
    _print_rows(rows, detailed=bool(job_id))
    return 0


def completed_result_rows(
    root: Path,
    job_id: str | None = None,
    benchmark_type: str = "tpcc",
    refresh_size: bool = False,
    inventory_path: Path | None = None,
    runner: Runner | None = None,
) -> list[dict[str, Any]]:
    benchmark_type = benchmark_type.lower()
    refresh_targets = _load_refresh_targets(root, inventory_path) if refresh_size and benchmark_type == "ts" else {}
    refresh_runner = runner or _run_local
    job_dirs = [find_job_dir(root, job_id)] if job_id else find_job_dirs(root)
    ids_by_run = result_id_map(root)
    rows: list[dict[str, Any]] = []
    for job_dir in [path for path in job_dirs if path is not None]:
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        job_name = str(plan.get("job_id") or job_dir.name)
        benchmark = _plan_benchmark_type(plan)
        if benchmark != benchmark_type:
            continue
        targets = _target_map(plan)
        for run in _runs_for_listing(root, job_name, benchmark, plan, benchmark_type):
            result = load_published_run_result(root, run) or _run_result(root, run)
            if result.get("status") != "success" and benchmark_type == "tpcc":
                continue
            target = targets.get(str(result.get("target_id")), {})
            canonical_run_id = str(run.get("run_id"))
            run_id = str(result.get("run_id"))
            result_id = ids_by_run.get((job_name, canonical_run_id), stable_result_id(job_name, canonical_run_id))
            result_type = _result_benchmark_type(benchmark, result)
            if result_type != benchmark_type:
                continue
            if result_type == "ts":
                if refresh_size:
                    _refresh_ts_table_size(root, run, result, refresh_targets, refresh_runner)
                rows.append(_ts_row(job_name, run_id, result_id, result, target))
                continue
            if result_type == "ap":
                rows.append(_ap_row(job_name, run_id, result_id, result, target))
                continue
            if result_type == "tpch":
                rows.append(_tpch_row(job_name, run_id, result_id, result, target))
                continue
            if result.get("measured_tpmc") is None or result.get("measured_tpmtotal") is None:
                continue
            rows.append(
                {
                    "benchmark": "tpcc",
                    "id": result_id,
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


def result_id_map(root: Path) -> dict[tuple[str, str], str]:
    records = sorted(_all_run_records(root))
    lengths = {record: RESULT_ID_MIN_LENGTH for record in records}
    while True:
        ids = {record: _hash_result_id(_result_identity(*record), lengths[record]) for record in records}
        collisions = _colliding_run_ids(ids)
        if not collisions:
            return ids
        if all(lengths[record] >= RESULT_ID_MAX_LENGTH for record in collisions):
            return ids
        for record in collisions:
            lengths[record] = min(RESULT_ID_MAX_LENGTH, lengths[record] + 2)


def unique_result_id(root: Path, job_id: str, run_id: str) -> str:
    return result_id_map(root).get((job_id, run_id), stable_result_id(job_id, run_id))


def legacy_result_id(job_id: str, run_id: str) -> str:
    prefix = f"{job_id}-"
    if run_id.startswith(prefix):
        return run_id[len(prefix):]
    return run_id


def _all_run_records(root: Path) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    for job_dir in find_job_dirs(root):
        try:
            plan = load_yaml(job_dir / "resolved-plan.yaml")
        except (OSError, ValueError):
            continue
        job_id = str(plan.get("job_id") or job_dir.name)
        benchmark = _plan_benchmark_type(plan)
        for run in _runs_for_listing(root, job_id, benchmark, plan, benchmark):
            run_id = str(run.get("run_id"))
            if run_id and run_id != "None":
                records.add((job_id, run_id))
    return records


def _colliding_run_ids(ids: dict[tuple[str, str], str]) -> set[tuple[str, str]]:
    seen: dict[str, tuple[str, str]] = {}
    collisions: set[tuple[str, str]] = set()
    for record, result_id in ids.items():
        previous = seen.get(result_id)
        if previous is None:
            seen[result_id] = record
            continue
        collisions.add(previous)
        collisions.add(record)
    return collisions


def _result_identity(job_id: str, run_id: str) -> str:
    return f"{job_id}\0{run_id}"


def _hash_result_id(run_id: str, length: int = RESULT_ID_MIN_LENGTH) -> str:
    return hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:length]


def _plan_benchmark_type(plan: dict[str, Any]) -> str:
    raw = plan.get("benchmark")
    if raw:
        return str(raw).lower()
    stage_sets = {
        "ts": {"ts-write", "ts-query", "point-query"},
        "ap": {"ap-query"},
        "tpch": {"tpch-load", "tpch-query"},
    }
    for run in plan.get("runs", []) if isinstance(plan.get("runs", []), list) else []:
        if not isinstance(run, dict):
            continue
        stage = run.get("stage")
        for benchmark, stages in stage_sets.items():
            if stage in stages:
                return benchmark
    return "tpcc"


def _runs_for_listing(root: Path, job_name: str, benchmark: str, plan: dict[str, Any], benchmark_type: str) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for run in plan.get("runs", []) if isinstance(plan.get("runs", []), list) else []:
        if isinstance(run, dict):
            runs[str(run.get("run_id"))] = dict(run)
    stage_sets = {
        "ts": {"ts-write", "ts-query", "point-query"},
        "ap": {"ap-query"},
        "tpch": {"tpch-load", "tpch-query"},
    }
    stages = stage_sets.get(benchmark_type)
    if stages is not None or benchmark in stage_sets:
        stages = stages or stage_sets[benchmark]
        for run_dir in sorted((root / "runs").glob(f"{job_name}-*")):
            result_path = run_dir / "result.json"
            if not result_path.exists():
                continue
            try:
                result = load_yaml(result_path)
            except (OSError, ValueError):
                continue
            if not isinstance(result, dict) or result.get("stage") not in stages:
                continue
            run_id = str(result.get("run_id") or run_dir.name)
            runs.setdefault(
                run_id,
                {
                    "run_id": run_id,
                    "target_id": result.get("target_id"),
                    "stage": result.get("stage"),
                    "run_dir": str(run_dir),
                    "benchmark_result_dir": result.get("result_dir") or result.get("benchmark_result_dir") or str(run_dir / "benchmark" / str(result.get("stage", ""))),
                },
            )
    return list(runs.values())


def _result_benchmark_type(plan_benchmark: str, result: dict[str, Any]) -> str:
    if plan_benchmark == "ts" or result.get("stage") in {"ts-write", "ts-query", "point-query"}:
        return "ts"
    if plan_benchmark == "ap" or result.get("stage") == "ap-query":
        return "ap"
    if plan_benchmark == "tpch" or result.get("stage") in {"tpch-load", "tpch-query"}:
        return "tpch"
    return "tpcc"


def _ts_row(job_name: str, run_id: str, result_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    row = {
        "benchmark": "ts",
        "id": result_id,
        "job": job_name,
        "run": run_id,
        "stage": result.get("stage"),
        "status": result.get("status"),
        "target": result.get("target_id"),
        "table": result.get("target_table"),
        "topic": result.get("kafka_topic"),
        "compress_threshold": result.get("compress_threshold"),
        "table_data_size": result.get("table_data_size", result.get("table_data_size_bytes")),
        "session_start": result.get("mxgate_write_start") or result.get("session_start") or result.get("started_at"),
        "session_end": result.get("mxgate_write_end") or result.get("session_end") or result.get("ended_at"),
        "elapsed_seconds": result.get("mxgate_elapsed_seconds") or result.get("elapsed_seconds"),
        "result_dir": result.get("result_dir") or result.get("benchmark_result_dir"),
        "db_host": target.get("db_host", "-"),
    }
    row.update(
        {
            "duration_seconds": result.get("duration_seconds"),
            "pressure_level": result.get("pressure_level"),
            "actual_qps": _actual_qps(result),
            "producer_actual_qps": _float_or_none(result.get("producer_actual_qps")),
            "produced_messages": result.get("produced_messages"),
            "producer_ack_qps": result.get("producer_ack_qps"),
            "written_rows": result.get("written_rows"),
            "final_lag": result.get("final_lag"),
            "max_lag": result.get("max_lag"),
            "errors": result.get("errors", result.get("producer_errors")),
            "error": result.get("error"),
            "rounds": result.get("rounds"),
            "sample_size": result.get("sample_size"),
            "query_count": result.get("query_count"),
            "avg_ms": result.get("avg_ms"),
            "p50_ms": result.get("p50_ms"),
            "p95_ms": result.get("p95_ms"),
            "p99_ms": result.get("p99_ms"),
            "rows_returned": result.get("rows_returned"),
            "hit_rate": result.get("hit_rate"),
        }
    )
    return row


def _ap_row(job_name: str, run_id: str, result_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "ap",
        "id": result_id,
        "job": job_name,
        "run": run_id,
        "stage": result.get("stage"),
        "status": result.get("status"),
        "target": result.get("target_id"),
        "db_host": target.get("db_host", "-"),
        "source_table": result.get("source_table", result.get("target_table")),
        "compress_threshold": result.get("compress_threshold"),
        "table_data_size": result.get("table_data_size", result.get("table_data_size_bytes")),
        "rounds": result.get("rounds"),
        "query_count": result.get("query_count"),
        "avg_ms": result.get("avg_ms"),
        "p50_ms": result.get("p50_ms"),
        "p95_ms": result.get("p95_ms"),
        "p99_ms": result.get("p99_ms"),
        "rows_returned": result.get("rows_returned"),
        "errors": result.get("errors"),
        "error": result.get("error"),
        "session_start": result.get("session_start") or result.get("started_at"),
        "session_end": result.get("session_end") or result.get("ended_at"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "result_dir": result.get("result_dir") or result.get("benchmark_result_dir"),
    }


def _tpch_row(job_name: str, run_id: str, result_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "tpch",
        "id": result_id,
        "job": job_name,
        "run": run_id,
        "stage": result.get("stage"),
        "status": result.get("status"),
        "target": result.get("target_id"),
        "db_host": target.get("db_host", "-"),
        "ddl_profile": result.get("ddl_profile"),
        "compress_threshold": result.get("compress_threshold"),
        "scale_factor": result.get("scale_factor"),
        "query_streams": result.get("query_streams"),
        "table_data_size": result.get("table_data_size", result.get("table_data_size_bytes")),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "qphh": result.get("qphh"),
        "query_count": result.get("query_count"),
        "avg_ms": result.get("avg_ms"),
        "p95_ms": result.get("p95_ms"),
        "errors": result.get("errors"),
        "error": result.get("error"),
        "session_start": result.get("session_start") or result.get("started_at"),
        "session_end": result.get("session_end") or result.get("ended_at"),
        "result_dir": result.get("result_dir") or result.get("benchmark_result_dir"),
    }


def stable_result_id(job_id: str, run_id: str) -> str:
    return _hash_result_id(_result_identity(job_id, run_id))


def _target_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for target in plan.get("targets", []) if isinstance(plan.get("targets", []), list) else []:
        connection = target.get("connection", {}) if isinstance(target.get("connection", {}), dict) else {}
        targets[str(target.get("id", "-"))] = {
            "db_host": connection.get("db_host", "-"),
        }
    return targets


def _load_refresh_targets(root: Path, inventory_path: Path | None) -> dict[str, Target]:
    path = inventory_path or root / "automan.yml"
    if not path.exists():
        raise ValueError(f"--refresh-size requires an inventory with real DB credentials: missing {path}")
    try:
        task = load_task_definition(root, path)
    except Exception as exc:
        raise ValueError(f"--refresh-size could not load inventory {path}: {exc}") from exc
    targets = {target.id: target for target in task.targets}
    if not targets:
        raise ValueError(f"--refresh-size inventory has no benchmark targets: {path}")
    return targets


def _refresh_ts_table_size(
    root: Path,
    run: dict[str, Any],
    result: dict[str, Any],
    targets: dict[str, Target],
    runner: Runner,
) -> None:
    target_id = str(result.get("target_id") or run.get("target_id") or "")
    target = targets.get(target_id)
    table = result.get("target_table")
    run_dir = _path_for_run(root, run, result)
    if target is None or not table or run_dir is None:
        result["size_refresh_error"] = "missing target, target_table, or run_dir"
        return
    spec = TsRunSpec(
        run_id=str(result.get("run_id") or run.get("run_id") or run_dir.name),
        target_id=target_id,
        stage=str(result.get("stage") or run.get("stage") or "ts-write"),
        compress_threshold=_int_or_zero(result.get("compress_threshold") or run.get("compress_threshold")),
        target_table=str(table),
        kafka_topic=str(result.get("kafka_topic") or ""),
        work_dir=root / "work" / "list-refresh" / run_dir.name,
        run_dir=run_dir,
        benchmark_dir=run_dir / "benchmark" / str(result.get("stage") or run.get("stage") or ""),
        database_dir=run_dir / "database",
        logs_dir=run_dir / "logs",
        collector_dir=run_dir / "collectors",
    )
    size = _table_data_size(target, spec, runner)
    if size.get("error") or size.get("table_data_size") is None:
        result["size_refresh_error"] = size.get("error") or "table size query returned no size"
        return
    result["table_data_size"] = size.get("table_data_size")
    result["table_data_size_bytes"] = size.get("table_data_size_bytes")
    spec.database_dir.mkdir(parents=True, exist_ok=True)
    write_json(spec.database_dir / "table-size.json", size)
    result_path = run_dir / "result.json"
    if result_path.exists():
        try:
            stored = load_yaml(result_path)
        except (OSError, ValueError):
            stored = None
        if isinstance(stored, dict):
            stored["table_data_size"] = result.get("table_data_size")
            stored["table_data_size_bytes"] = result.get("table_data_size_bytes")
            write_json(result_path, stored)


def _path_for_run(root: Path, run: dict[str, Any], result: dict[str, Any]) -> Path | None:
    raw = run.get("run_dir") or result.get("run_dir")
    if not raw:
        run_id = result.get("run_id") or run.get("run_id")
        if not run_id:
            return None
        return root / "runs" / str(run_id)
    path = Path(str(raw))
    return path if path.is_absolute() else root / path


def _int_or_zero(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _print_rows(rows: list[dict[str, Any]], detailed: bool) -> None:
    if any(row.get("benchmark") == "ts" for row in rows):
        _print_ts_rows(rows, detailed)
        return
    if any(row.get("benchmark") == "ap" for row in rows):
        _print_ap_rows(rows, detailed)
        return
    if any(row.get("benchmark") == "tpch" for row in rows):
        _print_tpch_rows(rows, detailed)
        return
    else:
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
    _print_table(rows, columns, _headers())


def _print_table(rows: list[dict[str, Any]], columns: list[str], headers: dict[str, str]) -> None:
    formatted = [{key: _format_cell(row.get(key)) for key in columns} for row in rows]
    widths = {
        key: max(len(headers[key]), *(len(row[key]) for row in formatted))
        for key in columns
    }
    print("  ".join(headers[key].ljust(widths[key]) for key in columns))
    print("  ".join("-" * widths[key] for key in columns))
    for row in formatted:
        print("  ".join(row[key].ljust(widths[key]) for key in columns))


def _print_ts_rows(rows: list[dict[str, Any]], detailed: bool) -> None:
    headers = _headers() if detailed else _ts_short_headers()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("stage") or "-"), []).append(row)
    stages = [stage for stage in ["ts-write", "ts-query", "point-query"] if stage in grouped]
    stages.extend(stage for stage in grouped if stage not in stages)
    multi_stage = len(stages) > 1
    for index, stage in enumerate(stages):
        if index > 0:
            print()
        if multi_stage:
            print(f"Stage: {stage}")
        _print_table(grouped[stage], _ts_columns_for_stage(stage, detailed), headers)


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _headers() -> dict[str, str]:
    return {
        "id": "ID",
        "job": "Job",
        "run": "Run",
        "stage": "Stage",
        "target": "Target",
        "db_host": "DB Host",
        "table": "Table",
        "source_table": "Source Table",
        "ddl_profile": "DDL Profile",
        "compress_threshold": "Compress Threshold",
        "table_data_size": "Table Data Size",
        "scale_factor": "SF",
        "query_streams": "Streams",
        "warehouse": "WH",
        "terminals": "Terminals",
        "run_mins": "Run Mins",
        "tpmc": "tpmC",
        "tpmtotal": "tpmTOTAL",
        "status": "Status",
        "session_start": "Start Time",
        "session_end": "End Time",
        "elapsed_seconds": "Elapsed Seconds",
        "result_dir": "Result Dir",
        "duration_seconds": "Duration Seconds",
        "pressure_level": "Pressure",
        "actual_qps": "Actual QPS",
        "producer_actual_qps": "Producer Actual QPS",
        "produced_messages": "Produced Messages",
        "producer_ack_qps": "Producer Ack QPS",
        "written_rows": "Written Rows",
        "final_lag": "Final Lag",
        "max_lag": "Max Lag",
        "errors": "Errors",
        "error": "Error",
        "rounds": "Rounds",
        "sample_size": "Sample Size",
        "query_count": "Query Count",
        "avg_ms": "Avg ms",
        "p50_ms": "P50 ms",
        "p95_ms": "P95 ms",
        "p99_ms": "P99 ms",
        "rows_returned": "Rows Returned",
        "hit_rate": "Hit Rate",
        "qphh": "Queries/h",
    }


def _ts_short_headers() -> dict[str, str]:
    headers = _headers()
    headers.update(
        {
            "compress_threshold": "CT",
            "table_data_size": "Size",
            "duration_seconds": "Dur(s)",
            "pressure_level": "Pressure",
            "actual_qps": "QPS",
            "produced_messages": "Produced",
            "written_rows": "Rows",
            "final_lag": "Lag",
            "max_lag": "MaxLag",
            "rounds": "Rounds",
            "sample_size": "Sample",
            "query_count": "Count",
            "avg_ms": "Avg",
            "p50_ms": "P50",
            "p95_ms": "P95",
            "p99_ms": "P99",
            "rows_returned": "Rows",
            "hit_rate": "Hit",
            "errors": "Err",
        }
    )
    return headers


def _ts_columns_for_stage(stage: str, detailed: bool) -> list[str]:
    common = [
        "id",
        "job",
        "stage",
        "status",
        "target",
        "table",
        "compress_threshold",
        "table_data_size",
        "session_start",
        "session_end",
    ]
    if not detailed:
        common = ["id", "status", "compress_threshold", "table_data_size"]
    if stage == "ts-write":
        optional = (
            ["elapsed_seconds", "duration_seconds", "pressure_level", "produced_messages", "actual_qps", "written_rows", "final_lag", "max_lag"]
            if detailed
            else ["duration_seconds", "pressure_level", "actual_qps", "written_rows", "final_lag", "max_lag"]
        )
    elif stage == "point-query":
        optional = ["rounds", "sample_size", "query_count", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "hit_rate", "errors"]
    else:
        optional = ["rounds", "query_count", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "rows_returned", "errors"]
    if detailed:
        return [*common, *optional, "result_dir"]
    return [*common, *optional]


def _print_ap_rows(rows: list[dict[str, Any]], detailed: bool) -> None:
    columns = (
        [
            "id",
            "job",
            "stage",
            "status",
            "target",
            "db_host",
            "source_table",
            "compress_threshold",
            "table_data_size",
            "rounds",
            "query_count",
            "avg_ms",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "rows_returned",
            "errors",
            "error",
            "session_end",
        ]
        if detailed
        else [
            "id",
            "status",
            "compress_threshold",
            "table_data_size",
            "rounds",
            "query_count",
            "avg_ms",
            "p95_ms",
            "rows_returned",
            "errors",
        ]
    )
    if detailed:
        columns.append("result_dir")
    _print_table(rows, columns, _headers())


def _print_tpch_rows(rows: list[dict[str, Any]], detailed: bool) -> None:
    columns = (
        [
            "id",
            "job",
            "stage",
            "status",
            "target",
            "db_host",
            "ddl_profile",
            "compress_threshold",
            "scale_factor",
            "query_streams",
            "table_data_size",
            "elapsed_seconds",
            "qphh",
            "query_count",
            "avg_ms",
            "p95_ms",
            "errors",
            "error",
            "session_end",
        ]
        if detailed
        else [
            "id",
            "stage",
            "status",
            "ddl_profile",
            "compress_threshold",
            "scale_factor",
            "query_streams",
            "table_data_size",
            "elapsed_seconds",
            "qphh",
            "errors",
        ]
    )
    if detailed:
        columns.append("result_dir")
    _print_table(rows, columns, _headers())


def _actual_qps(result: dict[str, Any]) -> float | None:
    existing = _float_or_none(result.get("actual_qps"))
    if existing is not None:
        return existing
    written_rows = _float_or_none(result.get("written_rows"))
    duration_seconds = _float_or_none(result.get("duration_seconds"))
    if written_rows is None or duration_seconds is None or duration_seconds <= 0:
        return None
    return round(written_rows / duration_seconds, 2)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
