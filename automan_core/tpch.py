from __future__ import annotations

import shlex
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from automan_core.collectors import CollectorError, CollectorManager, NullCollectorManager
from automan_core.config import write_json, write_yaml
from automan_core.models import CollectorConfig, Target, TpchBackendConfig, TpchConfig, TpchDataPrepareConfig, TpchRunSpec
from automan_core.sqlbench import (
    Runner,
    collector_config_dict,
    copy_tbl_file,
    latency_summary,
    load_sql_files,
    mark_run_finished,
    psql,
    psql_script,
    relation_size,
    render_sql_template,
    run_local,
    safe_name,
    set_job_status,
    set_run_status,
    tbl_path,
    timed_sql,
    update_job_state,
    write_command_result,
)
from automan_core.tpch_backend import YMatrixTpchBackend


TPCH_STAGES = ("tpch-load", "tpch-query")
TPCH_TABLES: dict[str, list[str]] = {
    "region": ["r_regionkey", "r_name", "r_comment"],
    "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
    "supplier": ["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone", "s_acctbal", "s_comment"],
    "customer": ["c_custkey", "c_name", "c_address", "c_nationkey", "c_phone", "c_acctbal", "c_mktsegment", "c_comment"],
    "part": ["p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size", "p_container", "p_retailprice", "p_comment"],
    "partsupp": ["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost", "ps_comment"],
    "orders": ["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice", "o_orderdate", "o_orderpriority", "o_clerk", "o_shippriority", "o_comment"],
    "lineitem": [
        "l_orderkey",
        "l_partkey",
        "l_suppkey",
        "l_linenumber",
        "l_quantity",
        "l_extendedprice",
        "l_discount",
        "l_tax",
        "l_returnflag",
        "l_linestatus",
        "l_shipdate",
        "l_commitdate",
        "l_receiptdate",
        "l_shipinstruct",
        "l_shipmode",
        "l_comment",
    ],
}
TPCH_LOAD_ORDER = ["region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem"]


def new_tpch_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-tpch")


def load_tpch_config(vars_: dict[str, Any]) -> TpchConfig:
    raw = _mapping(vars_.get("tpch"), "tpch")
    raw_prepare = dict(raw.get("data_prepare", {}) or {})
    raw_backend = dict(raw.get("backend", {}) or {})
    return TpchConfig(
        stages=_str_list(vars_.get("tpch_stages", list(TPCH_STAGES)), "tpch_stages"),
        compress_threshold=_int_list(vars_.get("compress_threshold", []), "compress_threshold"),
        scale_factors=_int_list(raw.get("scale_factors", []), "tpch.scale_factors"),
        query_streams=_int_list(raw.get("query_streams", []), "tpch.query_streams"),
        query_set=str(raw.get("query_set", "standard")),
        data_dir=str(raw.get("data_dir", "benchmarks/tpch/data/sf{scale_factor}")),
        schema_dir=str(raw.get("schema_dir", "benchmarks/tpch/schema")),
        query_dir=str(raw.get("query_dir", "benchmarks/tpch/queries")),
        data_prepare=TpchDataPrepareConfig(
            mode=str(raw_prepare.get("mode", "auto")),
            generator=str(raw_prepare.get("generator", "dbgen")),
            source_dir=str(raw_prepare.get("source_dir", "tools/tpch-dbgen")),
            build_command=str(raw_prepare.get("build_command", "make")),
            dbgen_command=str(raw_prepare.get("dbgen_command", "./dbgen")),
            force=_bool(raw_prepare.get("force", False)),
        ),
        backend=TpchBackendConfig(
            type=str(raw_backend.get("type", "ymatrix-tpch")),
            source_dir=str(raw_backend.get("source_dir", "tools/ymatrix-tpch")),
            remote_dir=str(raw_backend.get("remote_dir", "runs/{run_id}/ymatrix-tpch")),
            database_type=str(raw_backend.get("database_type", "matrixdb")),
            access_method=str(raw_backend.get("access_method", "mars3")),
            load_data_type=str(raw_backend.get("load_data_type", "mxgate")),
            optimizer=str(raw_backend.get("optimizer", "off")),
            preheating_data=_bool(raw_backend.get("preheating_data", False)),
            explain_analyze=_bool(raw_backend.get("explain_analyze", False)),
            greenplum_path=str(raw_backend.get("greenplum_path", "")),
            session_gucs=str(raw_backend.get("session_gucs", "set search_path to tpch; set statement_mem to '1GB';")),
        ),
    )


def validate_tpch_config(config: TpchConfig, targets: list[Target]) -> list[Any]:
    from automan_core.task_runner import ValidationMessage

    messages: list[ValidationMessage] = [ValidationMessage("OK", "benchmark: tpch")]
    invalid_stages = [stage for stage in config.stages if stage not in TPCH_STAGES]
    if invalid_stages:
        messages.append(ValidationMessage("FAIL", f"tpch_stages has unsupported stage(s): {', '.join(invalid_stages)}"))
    elif config.stages:
        messages.append(ValidationMessage("OK", f"tpch_stages: {', '.join(config.stages)}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpch_stages must not be empty"))

    messages.extend(_validate_positive_list("compress_threshold", config.compress_threshold))
    messages.extend(_validate_positive_list("tpch.scale_factors", config.scale_factors))
    messages.extend(_validate_positive_list("tpch.query_streams", config.query_streams))
    if config.query_set:
        messages.append(ValidationMessage("OK", f"tpch.query_set: {config.query_set}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpch.query_set is required"))

    if targets:
        messages.append(ValidationMessage("OK", f"{len(targets)} benchmark target(s) found"))
        for target in targets:
            messages.append(ValidationMessage("OK", f"{target.id}: tpch_ddl_profile={tpch_ddl_profile(target)}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpch benchmark requires at least one target"))
    return messages


def build_tpch_run_specs(root: Path, job_id: str, targets: list[Target], config: TpchConfig, stage: str | None) -> list[TpchRunSpec]:
    if stage is not None and stage not in TPCH_STAGES:
        raise ValueError("stage must be one of: tpch-load, tpch-query")
    stages = [stage] if stage else list(config.stages)
    specs: list[TpchRunSpec] = []
    for target in targets:
        thresholds = config.compress_threshold if target.profile.storage_engine == "mars3" else [None]
        ddl_profile = tpch_ddl_profile(target)
        for threshold in thresholds:
            for scale_factor in config.scale_factors:
                for query_streams in config.query_streams:
                    for stage_name in stages:
                        ct_part = f"-ct{threshold}" if threshold is not None else ""
                        run_id = f"{job_id}-{target.id}{ct_part}-sf{scale_factor}-q{query_streams}-{stage_name}"
                        run_dir = root / "runs" / run_id
                        specs.append(
                            TpchRunSpec(
                                run_id=run_id,
                                target_id=target.id,
                                stage=stage_name,
                                ddl_profile=ddl_profile,
                                compress_threshold=threshold,
                                scale_factor=scale_factor,
                                query_streams=query_streams,
                                run_dir=run_dir,
                                benchmark_dir=run_dir / "benchmark" / stage_name,
                                database_dir=run_dir / "database",
                                logs_dir=run_dir / "logs",
                                collector_dir=run_dir / "collectors",
                            )
                        )
    return specs


def write_tpch_job_files(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: TpchConfig,
    runs: list[TpchRunSpec],
    collectors: dict[str, Any] | None = None,
    stage: str | None = None,
) -> Path:
    job_dir = root / "runs" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "job_id": job_id,
        "benchmark": "tpch",
        "stage": stage or "full",
        "targets": [_target_dict(target) for target in targets],
        "matrix": {
            "compress_threshold": config.compress_threshold,
            "tpch_stages": config.stages,
            "scale_factors": config.scale_factors,
            "query_streams": config.query_streams,
        },
        "tpch": {
            **asdict(config),
            "stages": config.stages,
        },
        "collectors": collectors or {},
        "runs": [_run_dict(run) for run in runs],
        "archive": {
            "job_dir": str(job_dir),
            "job_yaml": str(job_dir / "job.yaml"),
            "resolved_plan": str(job_dir / "resolved-plan.yaml"),
            "job_state": str(job_dir / "job.json"),
            "status": str(job_dir / "status.json"),
        },
    }
    write_yaml(job_dir / "job.yaml", plan)
    write_yaml(job_dir / "resolved-plan.yaml", plan)
    write_json(
        job_dir / "job.json",
        {
            "job_id": job_id,
            "benchmark": "tpch",
            "status": "planned",
            "total_runs": len(runs),
            "finished_runs": 0,
            "success_runs": 0,
            "running_runs": 0,
            "failed_runs": 0,
            "pending_runs": len(runs),
            "last_error": None,
            "targets": [
                {
                    "target_id": target.id,
                    "execution_host": target.connection.execution_host,
                    "config_host": target.connection.ssh_host,
                    "database_host": target.connection.db_host,
                    "status": "pending",
                    "current_run": None,
                    "current_phase": None,
                    "finished_runs": 0,
                    "total_runs": sum(1 for run in runs if run.target_id == target.id),
                    "last_error": None,
                }
                for target in targets
            ],
        },
    )
    write_json(job_dir / "status.json", {"job_id": job_id, "benchmark": "tpch", "status": "planned"})
    for run in runs:
        run.run_dir.mkdir(parents=True, exist_ok=True)
        run.benchmark_dir.mkdir(parents=True, exist_ok=True)
        run.database_dir.mkdir(parents=True, exist_ok=True)
        run.logs_dir.mkdir(parents=True, exist_ok=True)
        run.collector_dir.mkdir(parents=True, exist_ok=True)
        write_yaml(run.run_dir / "resolved-task.yaml", _run_dict(run))
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})
    return job_dir


def execute_tpch_job(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: TpchConfig,
    runs: list[TpchRunSpec],
    collectors: CollectorConfig | dict | None = None,
    *,
    runner: Runner | None = None,
    backend_executor: Any | None = None,
) -> None:
    job_dir = root / "runs" / "jobs" / job_id
    _ensure_tpch_job_files(root, job_id, targets, config, runs, collectors)
    set_job_status(job_dir, "running")
    target_map = {target.id: target for target in targets}
    local_runner = runner or run_local
    for run in runs:
        target = target_map[run.target_id]
        try:
            _prepare_run_dirs(run)
            _execute_tpch_run(root, job_dir, target, config, run, collectors, local_runner, backend_executor)
        except Exception as exc:
            result = _failed_result(run, str(exc))
            write_json(run.run_dir / "result.json", result)
            write_json(run.benchmark_dir / "result.json", result)
            set_run_status(run.run_dir, "failed", run.stage, str(exc))
            mark_run_finished(job_dir, run.target_id, failed=True, last_error=str(exc))
    state_path = job_dir / "job.json"
    state = {}
    if state_path.exists():
        from automan_core.config import load_yaml

        state = load_yaml(state_path)
    if int(state.get("failed_runs", 0)) > 0:
        set_job_status(job_dir, "failed", state.get("last_error"))
    else:
        set_job_status(job_dir, "success")


def tpch_ddl_profile(target: Target) -> str:
    if target.profile.database_type == "postgresql":
        return "pg"
    if target.profile.storage_engine == "mars3":
        return "ym-mars3"
    return "ym-heap"


def _target_dict(target: Target) -> dict[str, Any]:
    return {
        "id": target.id,
        "display_name": target.profile.display_name,
        "database_type": target.profile.database_type,
        "storage_engine": target.profile.storage_engine,
        "test_mode": target.profile.test_mode,
        "tpch_ddl_profile": tpch_ddl_profile(target),
        "connection": target.connection.redacted(),
    }


def _run_dict(run: TpchRunSpec) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "ddl_profile": run.ddl_profile,
        "compress_threshold": run.compress_threshold,
        "scale_factor": run.scale_factor,
        "query_streams": run.query_streams,
        "run_dir": str(run.run_dir),
        "resolved_task_path": str(run.run_dir / "resolved-task.yaml"),
        "status_path": str(run.run_dir / "status.json"),
        "command_log_dir": str(run.logs_dir),
        "benchmark_parent_dir": str(run.run_dir / "benchmark"),
        "benchmark_result_dir": str(run.benchmark_dir),
        "database_dir": str(run.database_dir),
        "collector_dir": str(run.collector_dir),
    }


def _ensure_tpch_job_files(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: TpchConfig,
    runs: list[TpchRunSpec],
    collectors: CollectorConfig | dict | None,
) -> None:
    job_dir = root / "runs" / "jobs" / job_id
    if (job_dir / "job.json").exists():
        for run in runs:
            _prepare_run_dirs(run)
        return
    collector_plan = asdict(collectors) if isinstance(collectors, CollectorConfig) else collectors
    stages = {run.stage for run in runs}
    stage = next(iter(stages)) if len(stages) == 1 else None
    write_tpch_job_files(root, job_id, targets, config, runs, collector_plan, stage=stage)


def _prepare_run_dirs(run: TpchRunSpec) -> None:
    run.run_dir.mkdir(parents=True, exist_ok=True)
    run.benchmark_dir.mkdir(parents=True, exist_ok=True)
    run.database_dir.mkdir(parents=True, exist_ok=True)
    run.logs_dir.mkdir(parents=True, exist_ok=True)
    run.collector_dir.mkdir(parents=True, exist_ok=True)
    if not (run.run_dir / "status.json").exists():
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})


def _execute_tpch_run(
    root: Path,
    job_dir: Path,
    target: Target,
    config: TpchConfig,
    run: TpchRunSpec,
    collectors: CollectorConfig | dict | None,
    runner: Runner,
    backend_executor: Any | None = None,
) -> None:
    set_run_status(run.run_dir, "running", run.stage)
    update_job_state(job_dir, run.target_id, "running", run.run_id, run.stage)
    collector_manager = _collector_manager(root, target, run, collectors)
    collector_error = None
    try:
        collector_manager.start_phase(run.stage)
        if run.stage == "tpch-load":
            result = _run_tpch_load(root, target, config, run, runner, backend_executor)
        elif run.stage == "tpch-query":
            result = _run_tpch_query(root, target, config, run, runner, backend_executor)
        else:
            raise ValueError(f"unsupported tpch stage: {run.stage}")
    except CollectorError as exc:
        result = _failed_result(run, f"collector error: {exc}")
    finally:
        try:
            collector_manager.stop_phase(run.stage)
        except CollectorError as exc:
            collector_error = str(exc)
    if collector_error:
        result["status"] = "failed"
        result["error"] = f"{result.get('error')}; collector error: {collector_error}" if result.get("error") else f"collector error: {collector_error}"
    write_json(run.run_dir / "result.json", result)
    write_json(run.benchmark_dir / "result.json", result)
    status = str(result.get("status", "failed"))
    set_run_status(run.run_dir, status, run.stage, result.get("error"))
    mark_run_finished(job_dir, run.target_id, failed=status != "success", last_error=result.get("error"))


def _run_tpch_load(root: Path, target: Target, config: TpchConfig, run: TpchRunSpec, runner: Runner, backend_executor: Any | None = None) -> dict[str, Any]:
    if config.backend.type == "ymatrix-tpch":
        executor = backend_executor or YMatrixTpchBackend()
        return executor.run(root, target, config, run)
    if config.backend.type != "internal":
        return _failed_result(run, f"unsupported tpch.backend.type: {config.backend.type}")
    started = datetime.now().isoformat()
    prepare_result = prepare_tpch_data(root, config, run, runner)
    if prepare_result.get("status") == "failed":
        return _failed_result(run, str(prepare_result.get("error", "TPC-H data preparation failed")), started)
    data_error = _tpch_data_preflight(root, config, run.scale_factor)
    if data_error:
        return _failed_result(run, data_error, started)
    schema_sql = _load_schema_sql(root, config, run)
    ddl_result = psql_script(target, schema_sql, runner, timeout=7200, cwd=run.benchmark_dir)
    write_command_result(run.logs_dir, "tpch-ddl", ddl_result)
    if ddl_result.exit_code != 0:
        return _failed_result(run, ddl_result.stderr or ddl_result.stdout, started)

    data_dir = _tpch_data_dir(root, config, run.scale_factor)
    loaded_tables = 0
    load_errors: list[str] = []
    for table in TPCH_LOAD_ORDER:
        source = tbl_path(data_dir, table)
        if source is None:
            load_errors.append(f"missing data file for {table} under {data_dir}")
            continue
        result = copy_tbl_file(
            target,
            table=table,
            columns=TPCH_TABLES[table],
            source=source,
            work_dir=run.benchmark_dir / "copy",
            runner=runner,
            timeout=7200,
        )
        write_command_result(run.logs_dir, f"copy-{table}", result)
        if result.exit_code != 0:
            load_errors.append(f"{table}: {result.stderr or result.stdout}")
        else:
            loaded_tables += 1
    if load_errors:
        return _failed_result(run, "; ".join(load_errors), started)

    analyze = psql_script(target, "analyze;", runner, timeout=3600, cwd=run.benchmark_dir)
    write_command_result(run.logs_dir, "analyze", analyze)
    if analyze.exit_code != 0:
        return _failed_result(run, analyze.stderr or analyze.stdout, started)

    size = relation_size(target, TPCH_TABLES.keys(), runner)
    write_json(run.database_dir / "table-size.json", size)
    ended = datetime.now().isoformat()
    return {
        **_result_base(run),
        "loaded_tables": loaded_tables,
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": "success",
        "error": None,
        "session_start": started,
        "session_end": ended,
        "elapsed_seconds": _elapsed_seconds(started, ended),
        "result_dir": str(run.benchmark_dir),
    }


def _run_tpch_query(root: Path, target: Target, config: TpchConfig, run: TpchRunSpec, runner: Runner, backend_executor: Any | None = None) -> dict[str, Any]:
    if config.backend.type == "ymatrix-tpch":
        executor = backend_executor or YMatrixTpchBackend()
        return executor.run(root, target, config, run)
    if config.backend.type != "internal":
        return _failed_result(run, f"unsupported tpch.backend.type: {config.backend.type}")
    started = datetime.now().isoformat()
    query_dir = _tpch_query_dir(root, config)
    sql_files = load_sql_files(query_dir)
    timings: list[float] = []
    rows_returned = 0
    errors: list[str] = []
    query_count = 0
    stream_index = 0
    while stream_index < run.query_streams:
        stream_index += 1
        for sql_file in sql_files:
            sql = render_sql_template(sql_file.read_text(encoding="utf-8"), {"schema": "public"})
            output_path = run.benchmark_dir / "outputs" / f"stream{stream_index:03d}-{sql_file.stem}.out"
            elapsed_ms, row_count, error, result = timed_sql(target, sql, runner, timeout=7200, output_path=output_path)
            write_command_result(run.logs_dir, f"query-{stream_index:03d}-{safe_name(sql_file.stem)}", result)
            query_count += 1
            if error:
                errors.append(f"{sql_file.name}: {error}")
            else:
                timings.append(elapsed_ms)
                rows_returned += row_count
    ended = datetime.now().isoformat()
    elapsed_seconds = _elapsed_seconds(started, ended)
    size = relation_size(target, TPCH_TABLES.keys(), runner)
    write_json(run.database_dir / "table-size.json", size)
    error_count = len(errors)
    return {
        **_result_base(run),
        "query_count": query_count,
        **latency_summary(timings),
        "rows_returned": rows_returned,
        "errors": error_count,
        "queries_per_hour": round((query_count * 3600.0 / elapsed_seconds), 2) if elapsed_seconds > 0 and not error_count else None,
        "qphh": round((query_count * 3600.0 / elapsed_seconds), 2) if elapsed_seconds > 0 and not error_count else None,
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": "success" if query_count > 0 and error_count == 0 else "failed",
        "error": "; ".join(errors) if errors else None,
        "session_start": started,
        "session_end": ended,
        "elapsed_seconds": elapsed_seconds,
        "result_dir": str(run.benchmark_dir),
    }


def _load_schema_sql(root: Path, config: TpchConfig, run: TpchRunSpec) -> str:
    schema_dir = _resolve_root_path(root, config.schema_dir) / run.ddl_profile
    schema_file = schema_dir / "schema.sql"
    if not schema_file.exists():
        raise FileNotFoundError(f"TPC-H schema file not found: {schema_file}")
    threshold = "" if run.compress_threshold is None else str(run.compress_threshold)
    rendered = render_sql_template(schema_file.read_text(encoding="utf-8"), {"compress_threshold": threshold})
    return "set search_path to public;\n" + rendered


def _tpch_query_dir(root: Path, config: TpchConfig) -> Path:
    return _resolve_root_path(root, config.query_dir) / config.query_set


def _tpch_data_dir(root: Path, config: TpchConfig, scale_factor: int) -> Path:
    rendered = config.data_dir.format(scale_factor=scale_factor)
    return _resolve_root_path(root, rendered)


def _tpch_data_preflight(root: Path, config: TpchConfig, scale_factor: int) -> str | None:
    status = tpch_data_status(root, config, scale_factor)
    data_dir = status["data_dir"]
    if not data_dir.exists():
        return f"TPC-H data directory not found: {data_dir}"
    if status["missing"]:
        return f"TPC-H data file(s) missing under {data_dir}: {', '.join(status['missing'])}"
    if status["empty"]:
        return f"empty TPC-H data file(s) under {data_dir}: {', '.join(status['empty'])}"
    return None


def tpch_data_status(root: Path, config: TpchConfig, scale_factor: int) -> dict[str, Any]:
    data_dir = _tpch_data_dir(root, config, scale_factor)
    files = {table: tbl_path(data_dir, table) if data_dir.exists() else None for table in TPCH_LOAD_ORDER}
    missing = [table for table, path in files.items() if path is None]
    empty = [table for table, path in files.items() if path is not None and path.stat().st_size == 0]
    return {
        "data_dir": data_dir,
        "files": files,
        "missing": missing,
        "empty": empty,
        "ready": not missing and not empty,
    }


def prepare_tpch_data(root: Path, config: TpchConfig, run: TpchRunSpec, runner: Runner) -> dict[str, Any]:
    prepare = config.data_prepare
    mode = prepare.mode.lower()
    if mode == "skip":
        return {"status": "skipped", "reason": "data_prepare.mode=skip"}
    status = tpch_data_status(root, config, run.scale_factor)
    if status["ready"] and not prepare.force:
        return {"status": "ready", "data_dir": str(status["data_dir"])}
    if mode == "existing":
        return _data_prepare_failure(status)
    if mode != "auto":
        return {"status": "failed", "error": f"unsupported tpch.data_prepare.mode: {prepare.mode}"}

    data_dir = Path(status["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    build_result = _ensure_dbgen(root, config, runner, run.logs_dir)
    if build_result is not None and build_result.exit_code != 0:
        return {"status": "failed", "error": build_result.stderr or build_result.stdout or "dbgen build failed"}

    source_dir = _resolve_root_path(root, prepare.source_dir)
    command = _dbgen_command(source_dir, prepare.dbgen_command, run.scale_factor)
    started = datetime.now().isoformat()
    result = runner(["sh", "-lc", command], data_dir, 86400, None)
    write_command_result(run.logs_dir, "tpch-dbgen", result)
    if result.exit_code != 0:
        return {"status": "failed", "error": result.stderr or result.stdout or "dbgen failed"}
    final_status = tpch_data_status(root, config, run.scale_factor)
    if final_status["missing"] or final_status["empty"]:
        return _data_prepare_failure(final_status)
    ended = datetime.now().isoformat()
    manifest = {
        "status": "success",
        "scale_factor": run.scale_factor,
        "mode": mode,
        "generator": prepare.generator,
        "command": command,
        "started_at": started,
        "ended_at": ended,
        "data_dir": str(data_dir),
        "files": {
            table: {"path": str(path), "size_bytes": path.stat().st_size}
            for table, path in final_status["files"].items()
            if path is not None
        },
    }
    write_json(data_dir / "data-manifest.json", manifest)
    write_json(run.database_dir / "data-manifest.json", manifest)
    return manifest


def _data_prepare_failure(status: dict[str, Any]) -> dict[str, Any]:
    data_dir = status["data_dir"]
    if status["missing"]:
        return {"status": "failed", "error": f"TPC-H data file(s) missing under {data_dir}: {', '.join(status['missing'])}"}
    if status["empty"]:
        return {"status": "failed", "error": f"empty TPC-H data file(s) under {data_dir}: {', '.join(status['empty'])}"}
    return {"status": "failed", "error": f"TPC-H data is not ready under {data_dir}"}


def _ensure_dbgen(root: Path, config: TpchConfig, runner: Runner, logs_dir: Path) -> Any | None:
    prepare = config.data_prepare
    source_dir = _resolve_root_path(root, prepare.source_dir)
    dbgen_path = _dbgen_path(source_dir, prepare.dbgen_command)
    if dbgen_path.exists():
        return None
    if not source_dir.exists():
        return None
    command = prepare.build_command
    result = runner(["sh", "-lc", command], source_dir, 3600, None)
    write_command_result(logs_dir, "tpch-dbgen-build", result)
    return result


def _dbgen_command(source_dir: Path, dbgen_command: str, scale_factor: int) -> str:
    dbgen_path = _dbgen_path(source_dir, dbgen_command)
    executable = str(dbgen_path if dbgen_path.exists() else source_dir / dbgen_command)
    return f"DSS_CONFIG={shlex.quote(str(source_dir))} {shlex.quote(executable)} -s {scale_factor} -f"


def _dbgen_path(source_dir: Path, dbgen_command: str) -> Path:
    command = dbgen_command.strip()
    if command.startswith("./"):
        return source_dir / command[2:]
    path = Path(command)
    if path.is_absolute():
        return path
    return source_dir / command


def _resolve_root_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _result_base(run: TpchRunSpec) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "ddl_profile": run.ddl_profile,
        "compress_threshold": run.compress_threshold,
        "scale_factor": run.scale_factor,
        "query_streams": run.query_streams,
    }


def _failed_result(run: TpchRunSpec, error: str, started: str | None = None) -> dict[str, Any]:
    started = started or datetime.now().isoformat()
    return {
        **_result_base(run),
        "status": "failed",
        "error": str(error).strip(),
        "session_start": started,
        "session_end": datetime.now().isoformat(),
        "result_dir": str(run.benchmark_dir),
    }


def _collector_manager(root: Path, target: Target, run: TpchRunSpec, collectors: CollectorConfig | dict | None) -> CollectorManager | NullCollectorManager:
    config = collector_config_dict(root, collectors)
    if not config.get("enabled", True):
        return NullCollectorManager()
    return CollectorManager(root, target, run, config=config)


def _elapsed_seconds(started: str, ended: str) -> float:
    try:
        return round((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds(), 6)
    except ValueError:
        return 0.0


def _validate_positive_list(name: str, values: list[int], allow_zero: bool = False) -> list[Any]:
    from automan_core.task_runner import ValidationMessage

    threshold = 0 if allow_zero else 1
    if values and all(value >= threshold for value in values):
        return [ValidationMessage("OK", f"{name}: {', '.join(map(str, values))}")]
    return [ValidationMessage("FAIL", f"{name} must contain {'zero or ' if allow_zero else ''}positive integers")]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _int_list(value: Any, name: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return [int(item) for item in value]


def _str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return [str(item) for item in value]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in {"0", "false", "no", "off"}
    return bool(value)
