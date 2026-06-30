from __future__ import annotations

from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.result_summary import load_published_run_result
from automan_core.report import _run_result, find_job_dir, find_job_dirs


def show_completed_results(root: Path, job_id: str | None = None, benchmark_type: str = "tpcc") -> int:
    rows = completed_result_rows(root, job_id, benchmark_type)
    if job_id:
        print(f"Job: {job_id}")
        print(f"Finished Results: {len(rows)}")
        print()
    if not rows:
        print("No completed benchmark results found.")
        return 1
    _print_rows(rows, detailed=bool(job_id))
    return 0


def completed_result_rows(root: Path, job_id: str | None = None, benchmark_type: str = "tpcc") -> list[dict[str, Any]]:
    job_dirs = [find_job_dir(root, job_id)] if job_id else find_job_dirs(root)
    rows: list[dict[str, Any]] = []
    for job_dir in [path for path in job_dirs if path is not None]:
        plan = load_yaml(job_dir / "resolved-plan.yaml")
        job_name = str(plan.get("job_id") or job_dir.name)
        benchmark = str(plan.get("benchmark", "tpcc"))
        targets = _target_map(plan)
        for run in _runs_for_listing(root, job_name, benchmark, plan, benchmark_type):
            result = load_published_run_result(root, run) or _run_result(root, run)
            if result.get("status") != "success" and benchmark_type == "tpcc":
                continue
            target = targets.get(str(result.get("target_id")), {})
            run_id = str(result.get("run_id"))
            result_type = _result_benchmark_type(benchmark, result)
            if result_type != benchmark_type:
                continue
            if result_type == "ts":
                rows.append(_ts_row(job_name, run_id, result, target))
                continue
            if result_type == "ap":
                rows.append(_ap_row(job_name, run_id, result, target))
                continue
            if result_type == "tpch":
                rows.append(_tpch_row(job_name, run_id, result, target))
                continue
            if result.get("measured_tpmc") is None or result.get("measured_tpmtotal") is None:
                continue
            rows.append(
                {
                    "benchmark": "tpcc",
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


def _ts_row(job_name: str, run_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    row = {
        "benchmark": "ts",
        "id": stable_result_id(job_name, run_id),
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


def _ap_row(job_name: str, run_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "ap",
        "id": stable_result_id(job_name, run_id),
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


def _tpch_row(job_name: str, run_id: str, result: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "tpch",
        "id": stable_result_id(job_name, run_id),
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
        "run_mins": result.get("run_mins"),
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
            "run_mins",
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
