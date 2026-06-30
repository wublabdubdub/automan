from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from automan_core.collectors import CollectorError, CollectorManager, NullCollectorManager
from automan_core.config import write_json, write_yaml
from automan_core.models import ApConfig, ApQueryConfig, ApRunSpec, CollectorConfig, Target
from automan_core.sqlbench import (
    Runner,
    collector_config_dict,
    latency_summary,
    load_sql_files,
    mark_run_finished,
    relation_size,
    render_sql_template,
    run_local,
    safe_name,
    set_job_status,
    set_run_status,
    timed_sql,
    update_job_state,
    write_command_result,
)


AP_STAGES = ("ap-query",)


def new_ap_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-ap")


def load_ap_config(vars_: dict[str, Any]) -> ApConfig:
    return ApConfig(
        stages=_str_list(vars_.get("ap_stages", list(AP_STAGES)), "ap_stages"),
        compress_threshold=_int_list(vars_.get("compress_threshold", []), "compress_threshold"),
        query=_load_ap_query_config(_mapping(vars_.get("ap_query"), "ap_query")),
    )


def validate_ap_config(config: ApConfig, targets: list[Target]) -> list[Any]:
    from automan_core.task_runner import ValidationMessage

    messages: list[ValidationMessage] = [ValidationMessage("OK", "benchmark: ap")]
    invalid_stages = [stage for stage in config.stages if stage not in AP_STAGES]
    if invalid_stages:
        messages.append(ValidationMessage("FAIL", f"ap_stages has unsupported stage(s): {', '.join(invalid_stages)}"))
    elif config.stages:
        messages.append(ValidationMessage("OK", f"ap_stages: {', '.join(config.stages)}"))
    else:
        messages.append(ValidationMessage("FAIL", "ap_stages must not be empty"))

    messages.extend(_validate_positive_list("compress_threshold", config.compress_threshold))
    messages.extend(_validate_positive_list("ap_query.rounds", config.query.rounds))
    messages.extend(_validate_positive_list("ap_query.warmup_rounds", config.query.warmup_rounds, allow_zero=True))
    if config.query.source_table:
        messages.append(ValidationMessage("OK", f"ap_query.source_table: {config.query.source_table}"))
    else:
        messages.append(ValidationMessage("FAIL", "ap_query.source_table is required"))
    if config.query.query_set:
        messages.append(ValidationMessage("OK", f"ap_query.query_set: {config.query.query_set}"))
    else:
        messages.append(ValidationMessage("FAIL", "ap_query.query_set is required"))
    if config.query.timeout_seconds > 0:
        messages.append(ValidationMessage("OK", f"ap_query.timeout_seconds={config.query.timeout_seconds}"))
    else:
        messages.append(ValidationMessage("FAIL", "ap_query.timeout_seconds must be positive"))

    if targets:
        messages.append(ValidationMessage("OK", f"{len(targets)} benchmark target(s) found"))
    else:
        messages.append(ValidationMessage("FAIL", "ap benchmark requires at least one target"))
    return messages


def build_ap_run_specs(root: Path, job_id: str, targets: list[Target], config: ApConfig, stage: str | None) -> list[ApRunSpec]:
    if stage is not None and stage not in AP_STAGES:
        raise ValueError("stage must be one of: ap-query")
    stages = [stage] if stage else list(config.stages)
    specs: list[ApRunSpec] = []
    for threshold in config.compress_threshold:
        source_table = f"{config.query.source_table}_ct{threshold}"
        for target in targets:
            for stage_name in stages:
                for rounds in config.query.rounds:
                    for warmup_rounds in config.query.warmup_rounds:
                        run_id = f"{job_id}-{target.id}-ct{threshold}-{stage_name}-r{rounds}-w{warmup_rounds}"
                        run_dir = root / "runs" / run_id
                        specs.append(
                            ApRunSpec(
                                run_id=run_id,
                                target_id=target.id,
                                stage=stage_name,
                                compress_threshold=threshold,
                                source_table=source_table,
                                query_set=config.query.query_set,
                                rounds=rounds,
                                warmup_rounds=warmup_rounds,
                                run_dir=run_dir,
                                benchmark_dir=run_dir / "benchmark" / stage_name,
                                database_dir=run_dir / "database",
                                logs_dir=run_dir / "logs",
                                collector_dir=run_dir / "collectors",
                            )
                        )
    return specs


def write_ap_job_files(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: ApConfig,
    runs: list[ApRunSpec],
    collectors: dict[str, Any] | None = None,
    stage: str | None = None,
) -> Path:
    job_dir = root / "runs" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "job_id": job_id,
        "benchmark": "ap",
        "stage": stage or "full",
        "targets": [_target_dict(target) for target in targets],
        "matrix": {
            "compress_threshold": config.compress_threshold,
            "ap_stages": config.stages,
        },
        "ap_query": asdict(config.query),
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
            "benchmark": "ap",
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
    write_json(job_dir / "status.json", {"job_id": job_id, "benchmark": "ap", "status": "planned"})
    for run in runs:
        run.run_dir.mkdir(parents=True, exist_ok=True)
        run.benchmark_dir.mkdir(parents=True, exist_ok=True)
        run.database_dir.mkdir(parents=True, exist_ok=True)
        run.logs_dir.mkdir(parents=True, exist_ok=True)
        run.collector_dir.mkdir(parents=True, exist_ok=True)
        write_yaml(run.run_dir / "resolved-task.yaml", _run_dict(run))
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})
    return job_dir


def execute_ap_job(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: ApConfig,
    runs: list[ApRunSpec],
    collectors: CollectorConfig | dict | None = None,
    *,
    runner: Runner | None = None,
) -> None:
    job_dir = root / "runs" / "jobs" / job_id
    _ensure_ap_job_files(root, job_id, targets, config, runs, collectors)
    set_job_status(job_dir, "running")
    target_map = {target.id: target for target in targets}
    local_runner = runner or run_local
    for run in runs:
        target = target_map[run.target_id]
        try:
            _prepare_run_dirs(run)
            _execute_ap_run(root, job_dir, target, config, run, collectors, local_runner)
        except Exception as exc:
            result = _failed_result(run, str(exc))
            write_json(run.run_dir / "result.json", result)
            write_json(run.benchmark_dir / "result.json", result)
            set_run_status(run.run_dir, "failed", run.stage, str(exc))
            mark_run_finished(job_dir, run.target_id, failed=True, last_error=str(exc))
    from automan_core.config import load_yaml

    state = load_yaml(job_dir / "job.json")
    if int(state.get("failed_runs", 0)) > 0:
        set_job_status(job_dir, "failed", state.get("last_error"))
    else:
        set_job_status(job_dir, "success")


def _load_ap_query_config(raw: dict[str, Any]) -> ApQueryConfig:
    return ApQueryConfig(
        source_table=str(raw.get("source_table", "iot_vehicle_raw")),
        rounds=_int_list(raw.get("rounds", []), "ap_query.rounds"),
        warmup_rounds=_int_list(raw.get("warmup_rounds", []), "ap_query.warmup_rounds"),
        timeout_seconds=int(raw.get("timeout_seconds", 7200)),
        query_set=str(raw.get("query_set", "vehicle_ap_basic")),
    )


def _target_dict(target: Target) -> dict[str, Any]:
    return {
        "id": target.id,
        "display_name": target.profile.display_name,
        "database_type": target.profile.database_type,
        "storage_engine": target.profile.storage_engine,
        "test_mode": target.profile.test_mode,
        "connection": target.connection.redacted(),
    }


def _ensure_ap_job_files(
    root: Path,
    job_id: str,
    targets: list[Target],
    config: ApConfig,
    runs: list[ApRunSpec],
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
    write_ap_job_files(root, job_id, targets, config, runs, collector_plan, stage=stage)


def _prepare_run_dirs(run: ApRunSpec) -> None:
    run.run_dir.mkdir(parents=True, exist_ok=True)
    run.benchmark_dir.mkdir(parents=True, exist_ok=True)
    run.database_dir.mkdir(parents=True, exist_ok=True)
    run.logs_dir.mkdir(parents=True, exist_ok=True)
    run.collector_dir.mkdir(parents=True, exist_ok=True)
    if not (run.run_dir / "status.json").exists():
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})


def _execute_ap_run(
    root: Path,
    job_dir: Path,
    target: Target,
    config: ApConfig,
    run: ApRunSpec,
    collectors: CollectorConfig | dict | None,
    runner: Runner,
) -> None:
    set_run_status(run.run_dir, "running", run.stage)
    update_job_state(job_dir, run.target_id, "running", run.run_id, run.stage)
    collector_manager = _collector_manager(root, target, run, collectors)
    collector_error = None
    try:
        collector_manager.start_phase(run.stage)
        result = _run_ap_query(root, target, config, run, runner)
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


def _run_ap_query(root: Path, target: Target, config: ApConfig, run: ApRunSpec, runner: Runner) -> dict[str, Any]:
    started = datetime.now().isoformat()
    sql_dir = root / "benchmarks" / "ap" / "queries" / config.query.query_set
    sql_files = load_sql_files(sql_dir)
    timings: list[float] = []
    rows_returned = 0
    errors: list[str] = []
    query_count = 0
    for round_index in range(run.warmup_rounds + run.rounds):
        measuring = round_index >= run.warmup_rounds
        label = "run" if measuring else "warmup"
        visible_round = (round_index - run.warmup_rounds + 1) if measuring else (round_index + 1)
        for sql_file in sql_files:
            sql = render_sql_template(
                sql_file.read_text(encoding="utf-8"),
                {
                    "schema": "public",
                    "source_table": run.source_table,
                    "table": run.source_table,
                },
            )
            output_path = run.benchmark_dir / "outputs" / f"{label}{visible_round:03d}-{sql_file.stem}.out"
            elapsed_ms, row_count, error, result = timed_sql(target, sql, runner, timeout=config.query.timeout_seconds, output_path=output_path)
            write_command_result(run.logs_dir, f"{label}-{visible_round:03d}-{safe_name(sql_file.stem)}", result)
            if measuring:
                query_count += 1
                if error:
                    errors.append(f"{sql_file.name}: {error}")
                else:
                    timings.append(elapsed_ms)
                    rows_returned += row_count
    size = relation_size(target, [run.source_table], runner)
    write_json(run.database_dir / "table-size.json", size)
    ended = datetime.now().isoformat()
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "compress_threshold": run.compress_threshold,
        "source_table": run.source_table,
        "query_set": run.query_set,
        "rounds": run.rounds,
        "warmup_rounds": run.warmup_rounds,
        "query_count": query_count,
        **latency_summary(timings),
        "rows_returned": rows_returned,
        "errors": len(errors),
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": "success" if query_count > 0 and not errors else "failed",
        "error": "; ".join(errors) if errors else None,
        "session_start": started,
        "session_end": ended,
        "result_dir": str(run.benchmark_dir),
    }


def _failed_result(run: ApRunSpec, error: str) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "compress_threshold": run.compress_threshold,
        "source_table": run.source_table,
        "query_set": run.query_set,
        "status": "failed",
        "error": str(error).strip(),
        "session_start": datetime.now().isoformat(),
        "session_end": datetime.now().isoformat(),
        "result_dir": str(run.benchmark_dir),
    }


def _collector_manager(root: Path, target: Target, run: ApRunSpec, collectors: CollectorConfig | dict | None) -> CollectorManager | NullCollectorManager:
    config = collector_config_dict(root, collectors)
    if not config.get("enabled", True):
        return NullCollectorManager()
    return CollectorManager(root, target, run, config=config)


def _run_dict(run: ApRunSpec) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "compress_threshold": run.compress_threshold,
        "source_table": run.source_table,
        "query_set": run.query_set,
        "rounds": run.rounds,
        "warmup_rounds": run.warmup_rounds,
        "run_dir": str(run.run_dir),
        "resolved_task_path": str(run.run_dir / "resolved-task.yaml"),
        "status_path": str(run.run_dir / "status.json"),
        "command_log_dir": str(run.logs_dir),
        "benchmark_parent_dir": str(run.run_dir / "benchmark"),
        "benchmark_result_dir": str(run.benchmark_dir),
        "database_dir": str(run.database_dir),
        "collector_dir": str(run.collector_dir),
    }


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
