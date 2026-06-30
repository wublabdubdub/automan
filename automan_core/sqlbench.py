from __future__ import annotations

import gzip
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from automan_core.config import load_yaml, write_json
from automan_core.models import CollectorConfig, Target
from automan_core.ssh import CommandResult


Runner = Callable[[List[str], Path, int, Optional[Dict[str, str]]], CommandResult]


def run_local(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> CommandResult:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False, env=run_env)
        return CommandResult(" ".join(command), completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(" ".join(command), 124, ensure_text(exc.stdout), ensure_text(exc.stderr) or f"command timed out after {timeout} seconds")


def psql(target: Target, sql: str, runner: Runner, timeout: int, cwd: Path | None = None) -> CommandResult:
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
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
    ]
    return runner(command, cwd or Path.cwd(), timeout, {"PGPASSWORD": target.connection.db_password})


def psql_script(target: Target, sql: str, runner: Runner, timeout: int, cwd: Path | None = None) -> CommandResult:
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
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    return runner(command, cwd or Path.cwd(), timeout, {"PGPASSWORD": target.connection.db_password})


def render_sql_template(text: str, values: dict[str, str]) -> str:
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def load_sql_files(directory: Path) -> list[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"SQL directory not found: {directory}")
    files = sorted(path for path in directory.glob("*.sql") if path.is_file())
    if not files:
        raise FileNotFoundError(f"SQL directory has no .sql files: {directory}")
    return files


def timed_sql(target: Target, sql: str, runner: Runner, timeout: int, output_path: Path | None = None) -> tuple[float, int, str, CommandResult]:
    start = time.perf_counter()
    result = psql(target, sql, runner, timeout)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.stdout, encoding="utf-8")
    if result.exit_code != 0:
        return elapsed_ms, 0, result.stderr or result.stdout, result
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    return elapsed_ms, len(rows), "", result


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
    ordered = sorted(values)
    return {
        "avg_ms": sum(values) / len(values),
        "p50_ms": percentile(ordered, 50),
        "p95_ms": percentile(ordered, 95),
        "p99_ms": percentile(ordered, 99),
    }


def percentile(ordered: list[float], percentile_: int) -> float:
    index = min(len(ordered) - 1, max(0, int(round((percentile_ / 100) * (len(ordered) - 1)))))
    return ordered[index]


def relation_size(target: Target, table_names: Iterable[str], runner: Runner) -> dict[str, Any]:
    names = [name for name in table_names if name]
    if not names:
        return {"table_data_size": None, "table_data_size_bytes": None, "error": "no table names supplied"}
    values = ", ".join(sql_literal(name) for name in names)
    query = f"""
WITH rels AS (
  SELECT c.oid, c.relkind
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relname IN ({values})
    AND c.relkind IN ('r','p','')
),
size_oids AS (
  SELECT DISTINCT coalesce(ppt.relid, rels.oid) AS size_oid
  FROM rels
  LEFT JOIN LATERAL pg_catalog.pg_partition_tree(rels.oid) ppt ON rels.relkind = 'p'
),
sizes AS (
  SELECT sum(pg_catalog.pg_total_relation_size(size_oid))::bigint AS bytes
  FROM size_oids
)
SELECT coalesce(pg_catalog.pg_size_pretty(bytes), '0 bytes') || '|' || coalesce(bytes, 0)
FROM sizes;
"""
    result = psql(target, query, runner, timeout=300)
    if result.exit_code != 0:
        return {"table_data_size": None, "table_data_size_bytes": None, "error": result.stderr or result.stdout}
    text = result.stdout.strip()
    pretty, _, raw_bytes = text.partition("|")
    return {
        "table_data_size": pretty or None,
        "table_data_size_bytes": int_or_none(raw_bytes),
        "error": None,
    }


def table_has_rows(target: Target, table_name: str, runner: Runner) -> bool:
    exists = psql(target, f"select to_regclass('public.{ident(table_name)}') is not null;", runner, timeout=120)
    if exists.exit_code != 0 or not exists.stdout.strip().lower().endswith("t"):
        return False
    count = psql(target, f"select exists(select 1 from public.{ident(table_name)} limit 1);", runner, timeout=120)
    return count.exit_code == 0 and count.stdout.strip().lower().endswith("t")


def copy_tbl_file(
    target: Target,
    *,
    table: str,
    columns: list[str],
    source: Path,
    work_dir: Path,
    runner: Runner,
    timeout: int,
) -> CommandResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    transformed = work_dir / f"{source.stem}.copy"
    transform_tbl_file(source, transformed)
    column_list = ", ".join(ident(column) for column in columns)
    copy_sql = f"\\copy public.{ident(table)} ({column_list}) FROM {sql_literal(str(transformed))} WITH (FORMAT text, DELIMITER '|', NULL '')"
    return psql_script(target, copy_sql, runner, timeout=timeout, cwd=work_dir)


def transform_tbl_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8", errors="replace", newline="") as src, destination.open("w", encoding="utf-8", newline="") as dst:
        for line in src:
            dst.write(line.rstrip("\r\n").rstrip("|") + "\n")


def tbl_path(data_dir: Path, table: str) -> Path | None:
    candidates = [
        data_dir / f"{table}.tbl",
        data_dir / f"{table}.tbl.gz",
        data_dir / f"{table}.csv",
        data_dir / f"{table}.csv.gz",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def write_command_result(logs_dir: Path, phase: str, result: CommandResult) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{phase}.stdout.log"
    stderr_path = logs_dir / f"{phase}.stderr.log"
    stdout = ensure_text(result.stdout)
    stderr = ensure_text(result.stderr)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    write_json(
        logs_dir / f"{phase}.result.json",
        {
            "phase": phase,
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
            "recorded_at": datetime.now().isoformat(),
        },
    )


def set_job_status(job_dir: Path, status: str, last_error: str | None = None) -> None:
    data = load_yaml(job_dir / "status.json") if (job_dir / "status.json").exists() else {}
    data["status"] = status
    if last_error:
        data["last_error"] = last_error
    data["updated_at"] = datetime.now().isoformat()
    write_json(job_dir / "status.json", data)
    state_path = job_dir / "job.json"
    if state_path.exists():
        state = load_yaml(state_path)
        state["status"] = status
        if last_error:
            state["last_error"] = last_error
        write_json(state_path, state)


def set_run_status(run_dir: Path, status: str, phase: str | None, last_error: str | None = None) -> None:
    now = datetime.now().isoformat()
    previous = load_yaml(run_dir / "status.json") if (run_dir / "status.json").exists() else {}
    data = {"run_id": run_dir.name, "status": status, "phase": phase, "updated_at": now}
    if status == "running" and phase:
        data["phase_started_at"] = previous.get("phase_started_at") if previous.get("phase") == phase else now
    if last_error:
        data["last_error"] = last_error
    if status in {"success", "failed", "cancelled"}:
        data["phase_started_at"] = previous.get("phase_started_at")
        data["phase_finished_at"] = now
    write_json(run_dir / "status.json", data)


def update_job_state(job_dir: Path, target_id: str, status: str, current_run: str | None, current_phase: str | None) -> None:
    state = load_yaml(job_dir / "job.json")
    for target in state.get("targets", []):
        if target.get("target_id") == target_id:
            target["status"] = status
            target["current_run"] = current_run
            target["current_phase"] = current_phase
    state["running_runs"] = sum(1 for target in state.get("targets", []) if target.get("current_run"))
    write_json(job_dir / "job.json", state)


def mark_run_finished(job_dir: Path, target_id: str, failed: bool, last_error: str | None = None) -> None:
    state = load_yaml(job_dir / "job.json")
    state["finished_runs"] = int(state.get("finished_runs", 0)) + 1
    state["failed_runs"] = int(state.get("failed_runs", 0)) + (1 if failed else 0)
    state["success_runs"] = int(state.get("success_runs", 0)) + (0 if failed else 1)
    state["pending_runs"] = max(0, int(state.get("pending_runs", 0)) - 1)
    if last_error:
        state["last_error"] = last_error
    for target in state.get("targets", []):
        if target.get("target_id") == target_id:
            target["finished_runs"] = int(target.get("finished_runs", 0)) + 1
            target["current_run"] = None
            target["current_phase"] = None
            target["status"] = "failed" if failed else ("success" if target["finished_runs"] >= target.get("total_runs", 0) else "running")
            if failed and last_error:
                target["last_error"] = last_error
    state["running_runs"] = sum(1 for target in state.get("targets", []) if target.get("current_run"))
    write_json(job_dir / "job.json", state)


def collector_config_dict(root: Path, collectors: CollectorConfig | dict | None) -> dict[str, Any]:
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
                "mode": collectors.perf.mode,
                "sample_count": collectors.perf.sample_count,
                "sample_duration_seconds": collectors.perf.sample_duration_seconds,
                "sample_delay_seconds": collectors.perf.sample_delay_seconds,
                "sample_interval_seconds": collectors.perf.sample_interval_seconds,
                "sample_delay_ratio": collectors.perf.sample_delay_ratio,
                "sample_interval_ratio": collectors.perf.sample_interval_ratio,
            },
        }
    if isinstance(collectors, dict):
        return collectors
    config_path = root / "configs" / "collectors" / "default.yaml"
    if not config_path.exists():
        return {"enabled": False}
    config = load_yaml(config_path).get("collectors", {})
    return config if isinstance(config, dict) else {"enabled": False}


def ident(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"unsafe SQL identifier: {value}")
    return value


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def ensure_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
