from __future__ import annotations

import csv
import json
import os
import posixpath
import re
import shlex
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.collectors import CollectorError, CollectorManager, NullCollectorManager
from automan_core.models import (
    CollectorConfig,
    KafkaConfig,
    MxgateConfig,
    PointQueryConfig,
    Target,
    TsConfig,
    TsQueryConfig,
    TsRunSpec,
    TsWriteConfig,
)
from automan_core.ssh import CommandResult, SSHClient


TS_STAGES = ("ts-write", "ts-query", "point-query")
TS_QUERY_STAGES = {"ts-query", "point-query"}
DEFAULT_PRESSURE_PROFILES: dict[str, dict[str, object]] = {
    "smoke": {"producer_type": "kafka_perf", "target_qps": 1000, "producers": 1, "batch_size": 1000, "linger_ms": 10, "payload_bytes": 128, "sample_records": 10000},
    "low": {"producer_type": "kafka_perf", "target_qps": 10000, "producers": 2, "batch_size": 5000, "linger_ms": 10, "payload_bytes": 128, "sample_records": 10000},
    "medium": {"producer_type": "kafka_perf", "target_qps": 50000, "producers": 4, "batch_size": 10000, "linger_ms": 5, "payload_bytes": 128, "sample_records": 10000},
    "high": {"producer_type": "kafka_perf", "target_qps": 200000, "producers": 8, "batch_size": 20000, "linger_ms": 5, "payload_bytes": 128, "sample_records": 10000},
    "extreme": {"producer_type": "kafka_perf", "target_qps": 500000, "producers": 16, "batch_size": 50000, "linger_ms": 1, "payload_bytes": 128, "sample_records": 20000},
}


def new_ts_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-ts")


def load_ts_config(vars_: dict[str, Any]) -> TsConfig:
    return TsConfig(
        stages=_str_list(vars_.get("ts_stages", list(TS_STAGES)), "ts_stages"),
        compress_threshold=_int_list(vars_.get("compress_threshold", []), "compress_threshold"),
        kafka=_load_kafka_config(_mapping(vars_.get("kafka"), "kafka")),
        mxgate=_load_mxgate_config(_mapping(vars_.get("mxgate"), "mxgate")),
        write=_load_ts_write_config(_mapping(vars_.get("ts_write"), "ts_write")),
        query=_load_ts_query_config(_mapping(vars_.get("ts_query"), "ts_query")),
        point_query=_load_point_query_config(_mapping(vars_.get("point_query"), "point_query")),
    )


def validate_ts_config(config: TsConfig, targets: list[Target]) -> list[Any]:
    from automan_core.task_runner import ValidationMessage

    messages: list[ValidationMessage] = [ValidationMessage("OK", "benchmark: ts")]
    invalid_stages = [stage for stage in config.stages if stage not in TS_STAGES]
    if invalid_stages:
        messages.append(ValidationMessage("FAIL", f"ts_stages has unsupported stage(s): {', '.join(invalid_stages)}"))
    elif config.stages:
        messages.append(ValidationMessage("OK", f"ts_stages: {', '.join(config.stages)}"))
    else:
        messages.append(ValidationMessage("FAIL", "ts_stages must not be empty"))

    if config.compress_threshold and all(value > 0 for value in config.compress_threshold):
        messages.append(ValidationMessage("OK", f"compress_threshold: {', '.join(map(str, config.compress_threshold))}"))
    else:
        messages.append(ValidationMessage("FAIL", "compress_threshold must contain positive integers"))

    if config.kafka.topic:
        messages.append(ValidationMessage("OK", f"kafka.topic: {config.kafka.topic}"))
    else:
        messages.append(ValidationMessage("FAIL", "kafka.topic is required"))
    if config.kafka.host and config.kafka.kafka_bin and config.kafka.bootstrap_server:
        messages.append(ValidationMessage("OK", f"kafka: {config.kafka.host} {config.kafka.bootstrap_server}"))
    else:
        messages.append(ValidationMessage("FAIL", "kafka.host, kafka.kafka_bin and kafka.bootstrap_server are required"))
    if config.kafka.partitions and all(value > 0 for value in config.kafka.partitions):
        messages.append(ValidationMessage("OK", f"kafka.partitions: {', '.join(map(str, config.kafka.partitions))}"))
    else:
        messages.append(ValidationMessage("FAIL", "kafka.partitions must contain positive integers"))
    if config.kafka.replication_factor > 0:
        messages.append(ValidationMessage("OK", f"kafka.replication_factor={config.kafka.replication_factor}"))
    else:
        messages.append(ValidationMessage("FAIL", "kafka.replication_factor must be positive"))

    messages.extend(_validate_positive_list("ts_write.workers", config.write.workers))
    messages.extend(_validate_positive_list("ts_write.rate_per_worker", config.write.rate_per_worker))
    messages.extend(_validate_positive_list("ts_write.vins", config.write.vins))
    messages.extend(_validate_positive_list("ts_write.duration_seconds", config.write.duration_seconds))
    pressure_profile = _pressure_profile(config)
    if pressure_profile:
        messages.append(
            ValidationMessage(
                "OK",
                f"ts_write.pressure_level: {config.write.pressure_level} target_qps={_profile_int(pressure_profile, 'target_qps', 0)} producers={_profile_int(pressure_profile, 'producers', 0)}",
            )
        )
    else:
        messages.append(ValidationMessage("FAIL", f"ts_write.pressure_level has no matching profile: {config.write.pressure_level}"))
    messages.extend(_validate_positive_list("ts_query.rounds", config.query.rounds))
    messages.extend(_validate_positive_list("ts_query.warmup_rounds", config.query.warmup_rounds, allow_zero=True))
    messages.extend(_validate_positive_list("point_query.rounds", config.point_query.rounds))
    messages.extend(_validate_positive_list("point_query.warmup_rounds", config.point_query.warmup_rounds, allow_zero=True))
    messages.extend(_validate_positive_list("point_query.sample_size", config.point_query.sample_size))

    for name, mode in (("ts_query.dependency_mode", config.query.dependency_mode), ("point_query.dependency_mode", config.point_query.dependency_mode)):
        if mode in {"reuse", "auto-write"}:
            messages.append(ValidationMessage("OK", f"{name}: {mode}"))
        else:
            messages.append(ValidationMessage("FAIL", f"{name} must be one of: reuse, auto-write"))

    if len(targets) == 1:
        target = targets[0]
        messages.append(ValidationMessage("OK", f"1 benchmark target found"))
        if target.profile.database_type == "ymatrix" and target.profile.storage_engine == "mars3":
            messages.append(ValidationMessage("OK", f"{target.id}: YMatrix MARS3 target"))
        else:
            messages.append(ValidationMessage("FAIL", f"{target.id}: ts benchmark requires a YMatrix MARS3 target in phase one"))
    else:
        messages.append(ValidationMessage("FAIL", "ts benchmark requires exactly one YMatrix target in phase one"))
    return messages


def build_ts_run_specs(root: Path, job_id: str, target: Target, config: TsConfig, stage: str | None) -> list[TsRunSpec]:
    if stage is not None and stage not in TS_STAGES:
        raise ValueError("stage must be one of: ts-write, ts-query, point-query")
    stages = [stage] if stage else list(config.stages)
    specs: list[TsRunSpec] = []
    for threshold in config.compress_threshold:
        for stage_name in stages:
            target_table = ts_table_name(config.write.table, threshold)
            run_id = f"{job_id}-{target.id}-ct{threshold}-{stage_name}"
            run_dir = root / "runs" / run_id
            specs.append(
                TsRunSpec(
                    run_id=run_id,
                    target_id=target.id,
                    stage=stage_name,
                    compress_threshold=threshold,
                    target_table=target_table,
                    kafka_topic=config.kafka.topic,
                    work_dir=root / "work" / "ts" / run_id,
                    run_dir=run_dir,
                    benchmark_dir=run_dir / "benchmark" / stage_name,
                    database_dir=run_dir / "database",
                    logs_dir=run_dir / "logs",
                    collector_dir=run_dir / "collectors",
                    run_mins=max(1, int(config.write.duration_seconds[0] / 60)),
                )
            )
    return specs


def ts_table_name(base_table: str, compress_threshold: int) -> str:
    return f"{base_table}_ct{compress_threshold}"


def write_ts_job_files(
    root: Path,
    job_id: str,
    target: Target,
    config: TsConfig,
    runs: list[TsRunSpec],
    collectors: dict[str, Any] | None = None,
    stage: str | None = None,
) -> Path:
    job_dir = root / "runs" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "job_id": job_id,
        "benchmark": "ts",
        "stage": stage or "full",
        "targets": [
            {
                "id": target.id,
                "display_name": target.profile.display_name,
                "database_type": target.profile.database_type,
                "storage_engine": target.profile.storage_engine,
                "test_mode": target.profile.test_mode,
                "connection": target.connection.redacted(),
            }
        ],
        "matrix": {
            "compress_threshold": config.compress_threshold,
            "ts_stages": config.stages,
        },
        "kafka": _redacted_kafka(config.kafka),
        "mxgate": _redacted_mxgate(config.mxgate),
        "ts_write": asdict(config.write),
        "ts_query": asdict(config.query),
        "point_query": asdict(config.point_query),
        "collectors": collectors or {},
        "runs": [_run_dict(run) for run in runs],
        "archive": {
            "job_dir": str(job_dir),
            "job_yaml": str(job_dir / "job.yaml"),
            "resolved_plan": str(job_dir / "resolved-plan.yaml"),
            "job_state": str(job_dir / "job.json"),
            "status": str(job_dir / "status.json"),
            "timeline": str(job_dir / "timeline.jsonl"),
            "report_dir": str(job_dir / "report"),
            "report_markdown": str(job_dir / "report" / "report.md"),
        },
        "per_run_sequence": [run.stage for run in runs],
        "destroy_policy": "kafka_topic_changes_only_via_kafka_check_apply",
    }
    write_yaml(job_dir / "job.yaml", plan)
    write_yaml(job_dir / "resolved-plan.yaml", plan)
    write_json(
        job_dir / "job.json",
        {
            "job_id": job_id,
            "benchmark": "ts",
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
                    "kafka_host": config.kafka.host,
                    "mxgate_host": target.connection.db_host,
                    "status": "pending",
                    "current_run": None,
                    "current_phase": None,
                    "finished_runs": 0,
                    "total_runs": len(runs),
                    "last_error": None,
                }
            ],
        },
    )
    write_json(job_dir / "status.json", {"job_id": job_id, "benchmark": "ts", "status": "planned"})
    for run in runs:
        run.run_dir.mkdir(parents=True, exist_ok=True)
        run.benchmark_dir.mkdir(parents=True, exist_ok=True)
        run.database_dir.mkdir(parents=True, exist_ok=True)
        run.logs_dir.mkdir(parents=True, exist_ok=True)
        write_yaml(run.run_dir / "resolved-task.yaml", _run_dict(run))
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})
    return job_dir


def execute_ts_job(
    root: Path,
    job_id: str,
    target: Target,
    config: TsConfig,
    runs: list[TsRunSpec],
    collectors: CollectorConfig | dict | None = None,
    *,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult] | None = None,
    ssh_factory: Callable[[str, int, str, str], SSHClient] = SSHClient,
) -> None:
    job_dir = root / "runs" / "jobs" / job_id
    _ensure_ts_job_files(root, job_id, target, config, runs, collectors)
    _set_job_status(job_dir, "running")
    local_runner = runner or _run_local
    for run in runs:
        try:
            _prepare_ts_run_dirs(run)
            _execute_ts_run(root, job_dir, target, config, run, local_runner, ssh_factory, collectors)
        except Exception as exc:
            error = str(exc)
            _set_run_status(run.run_dir, "failed", run.stage, error)
            _ensure_ts_job_files(root, job_id, target, config, runs, collectors)
            _mark_run_finished(job_dir, target.id, failed=True, last_error=error)
    state = load_yaml(job_dir / "job.json")
    if int(state.get("failed_runs", 0)) > 0:
        _set_job_status(job_dir, "failed", state.get("last_error"))
    else:
        _set_job_status(job_dir, "success")


def _ensure_ts_job_files(
    root: Path,
    job_id: str,
    target: Target,
    config: TsConfig,
    runs: list[TsRunSpec],
    collectors: CollectorConfig | dict | None,
) -> None:
    job_dir = root / "runs" / "jobs" / job_id
    if (job_dir / "job.json").exists():
        for run in runs:
            _prepare_ts_run_dirs(run)
        return
    collector_plan = asdict(collectors) if isinstance(collectors, CollectorConfig) else collectors
    stages = {run.stage for run in runs}
    stage = next(iter(stages)) if len(stages) == 1 else None
    write_ts_job_files(root, job_id, target, config, runs, collector_plan, stage=stage)


def _prepare_ts_run_dirs(run: TsRunSpec) -> None:
    run.run_dir.mkdir(parents=True, exist_ok=True)
    run.work_dir.mkdir(parents=True, exist_ok=True)
    run.benchmark_dir.mkdir(parents=True, exist_ok=True)
    run.database_dir.mkdir(parents=True, exist_ok=True)
    run.logs_dir.mkdir(parents=True, exist_ok=True)
    run.collector_dir.mkdir(parents=True, exist_ok=True)
    if not (run.run_dir / "status.json").exists():
        write_json(run.run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})


def kafka_check(root: Path, inventory: Path, apply: bool = False, ssh_factory: Callable[[str, int, str, str], SSHClient] = SSHClient) -> int:
    from automan_core.task_runner import load_task_definition

    task = load_task_definition(root, inventory)
    if task.benchmark != "ts" or task.ts_config is None:
        print("[FAIL] kafka-check requires benchmark: ts inventory")
        return 1
    config = task.ts_config.kafka
    partition = config.partitions[0]
    commands = kafka_topic_commands(config, partition)
    client = ssh_factory(config.host, config.port, config.user, config.password)
    checks = [
        (f"test -x {shlex.quote(config.kafka_bin.rstrip('/') + '/kafka-topics.sh')}", "kafka-topics.sh exists"),
        (f"{shlex.quote(config.kafka_bin.rstrip('/') + '/kafka-topics.sh')} --bootstrap-server {shlex.quote(config.bootstrap_server)} --list >/dev/null", "bootstrap reachable"),
        (f"{shlex.quote(config.kafka_bin.rstrip('/') + '/kafka-topics.sh')} --bootstrap-server {shlex.quote(config.bootstrap_server)} --describe --topic {shlex.quote(config.topic)}", "topic describe"),
    ]
    failures = 0
    for command, label in checks:
        result = client.run(command, 120)
        if result.exit_code == 0:
            print(f"[ OK ] kafka {label}")
        else:
            level = "WARN" if label == "topic describe" else "FAIL"
            print(f"[{level}] kafka {label}: {(result.stderr or result.stdout).strip()}")
            if level == "FAIL":
                failures += 1
    print("[HINT] kafka-check --apply will execute:")
    for command in commands:
        print(command)
    if failures or not apply:
        return failures
    script = "\n".join(["set -e", *commands])
    result = client.run(script, 600)
    if result.exit_code == 0:
        print("[ OK ] kafka topic prepared")
        if result.stdout.strip():
            print(result.stdout.rstrip())
        return 0
    print(f"[FAIL] kafka topic prepare failed: {(result.stderr or result.stdout).strip()}")
    return 1


def kafka_topic_commands(config: KafkaConfig, partitions: int) -> list[str]:
    kafka_bin = config.kafka_bin.rstrip("/")
    bootstrap = shlex.quote(config.bootstrap_server)
    topic = shlex.quote(config.topic)
    return [
        f"export KAFKA_BIN={shlex.quote(kafka_bin)}",
        'export PATH="/usr/local/go/bin:$KAFKA_BIN:$PATH"',
        f'$KAFKA_BIN/kafka-topics.sh --bootstrap-server {bootstrap} --delete --topic {topic} || true',
        "sleep 5",
        f"$KAFKA_BIN/kafka-topics.sh --bootstrap-server {bootstrap} --create --topic {topic} --partitions {int(partitions)} --replication-factor {int(config.replication_factor)}",
        f"$KAFKA_BIN/kafka-topics.sh --bootstrap-server {bootstrap} --describe --topic {topic}",
    ]


def _execute_ts_run(
    root: Path,
    job_dir: Path,
    target: Target,
    config: TsConfig,
    run: TsRunSpec,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult],
    ssh_factory: Callable[[str, int, str, str], SSHClient],
    collectors: CollectorConfig | dict | None,
) -> None:
    _set_run_status(run.run_dir, "running", run.stage)
    _update_job_state(job_dir, target.id, "running", run.run_id, run.stage)
    collector_manager = _collector_manager(root, target, run, collectors)
    collector_error = None
    try:
        collector_manager.start_phase(run.stage)
        if run.stage == "ts-write":
            result = _run_ts_write(root, target, config, run, runner, ssh_factory)
        elif run.stage == "ts-query":
            result = _run_ts_query(root, target, config, run, runner, ssh_factory)
        elif run.stage == "point-query":
            result = _run_point_query(root, target, config, run, runner, ssh_factory)
        else:
            raise ValueError(f"unsupported ts stage: {run.stage}")
    except CollectorError as exc:
        result = _failed_ts_result(run, datetime.now().isoformat(), f"collector error: {exc}")
    finally:
        try:
            collector_manager.stop_phase(run.stage)
        except CollectorError as exc:
            collector_error = str(exc)
    if collector_error:
        result["status"] = "failed"
        result["error"] = f"{result.get('error')}; collector error: {collector_error}" if result.get("error") else f"collector error: {collector_error}"
    write_json(run.run_dir / "result.json", result)
    stage_result = run.benchmark_dir / "result.json"
    write_json(stage_result, result)
    status = str(result.get("status", "failed"))
    _set_run_status(run.run_dir, status, run.stage, result.get("error"))
    _mark_run_finished(job_dir, target.id, failed=status != "success", last_error=result.get("error"))


def _run_ts_write(
    root: Path,
    target: Target,
    config: TsConfig,
    run: TsRunSpec,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult],
    ssh_factory: Callable[[str, int, str, str], SSHClient],
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    _prepare_ts_run_dirs(run)
    mxgate_conf = run.benchmark_dir / "mxgate.conf"
    _write_mxgate_conf(mxgate_conf, target, config, run)
    if not mxgate_conf.exists():
        raise FileNotFoundError(f"mxgate config was not written: {mxgate_conf}")
    ddl = _ts_table_ddl(run.target_table, run.compress_threshold)
    ddl_result = _psql(target, ddl, runner, timeout=300)
    _write_command_result(run.logs_dir, "ts-ddl", ddl_result)
    if ddl_result.exit_code != 0:
        return _failed_ts_result(run, started, f"DDL failed: {ddl_result.stderr or ddl_result.stdout}")

    kafka = ssh_factory(config.kafka.host, config.kafka.port, config.kafka.user, config.kafka.password)
    mxgate = ssh_factory(target.connection.db_host, target.connection.ssh_port, config.mxgate.user, config.mxgate.password)
    producer_start_offset = _kafka_topic_log_end(kafka, config)
    producer = kafka.run(_producer_start_command(config, run), 120)
    _write_text(run.logs_dir / "producer-start.log", producer.stdout + producer.stderr)
    if producer.exit_code != 0:
        return _failed_ts_result(run, started, f"producer start failed: {producer.stderr or producer.stdout}")
    producer_pid = producer.stdout.strip().splitlines()[-1] if producer.stdout.strip() else ""
    time.sleep(max(0, min(config.write.producer_lead_seconds, config.write.duration_seconds[0])))
    remote_conf = f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}/mxgate.conf"
    put_conf = mxgate.run(_remote_write_file_command(remote_conf, mxgate_conf.read_text(encoding="utf-8")), 120)
    _write_text(run.logs_dir / "mxgate-conf.log", put_conf.stdout + put_conf.stderr)
    if put_conf.exit_code != 0:
        kafka.run(_stop_process_command(producer_pid), 60)
        return _failed_ts_result(run, started, f"mxgate config upload failed: {put_conf.stderr or put_conf.stdout}")
    start = mxgate.run(_mxgate_start_command(config, remote_conf, run), 120)
    _write_text(run.logs_dir / "mxgate-start.log", start.stdout + start.stderr)
    if start.exit_code != 0:
        kafka.run(_stop_process_command(producer_pid), 60)
        return _failed_ts_result(run, started, f"mxgate start failed: {start.stderr or start.stdout}")
    mxgate_pid = _extract_mxgate_pid(start.stdout + start.stderr)
    if not mxgate_pid:
        kafka.run(_stop_process_command(producer_pid), 60)
        return _failed_ts_result(run, started, "mxgate start did not report a verified pid")
    _write_text(run.logs_dir / "mxgate.pid", mxgate_pid + "\n")

    max_lag = 0
    final_lag = 0
    deadline = time.time() + max(1, config.write.duration_seconds[0])
    while time.time() < deadline:
        lag = _kafka_lag(kafka, config, run)
        max_lag = max(max_lag, lag)
        final_lag = lag
        _append_csv(run.logs_dir / "kafka-lag.csv", ["time", "lag"], [datetime.now().isoformat(), str(lag)])
        time.sleep(max(1, min(config.write.monitor_interval_seconds, 5)))
    kafka.run(_stop_process_command(producer_pid), 60)

    drain_deadline = time.time() + max(0, config.write.lag_drain_timeout_seconds)
    while time.time() < drain_deadline:
        final_lag = _kafka_lag(kafka, config, run)
        max_lag = max(max_lag, final_lag)
        _append_csv(run.logs_dir / "kafka-lag.csv", ["time", "lag"], [datetime.now().isoformat(), str(final_lag)])
        if final_lag == 0:
            break
        time.sleep(max(1, min(config.write.monitor_interval_seconds, 5)))
    status_log = mxgate.run(_mxgate_status_command(config, run), 120)
    _write_text(run.logs_dir / "mxgate-status.log", status_log.stdout + status_log.stderr)
    daemon_log = mxgate.run(_mxgate_log_command(config, run), 120)
    _write_text(run.logs_dir / "mxgate.log", daemon_log.stdout + daemon_log.stderr)
    stop = mxgate.run(_mxgate_stop_command(config, run), 120)
    _write_text(run.logs_dir / "mxgate-stop.log", stop.stdout + stop.stderr)
    producer_log = kafka.run(f"cat /tmp/automan-ts-{shlex.quote(run.run_id)}.producer.log* 2>/dev/null || true", 120)
    _write_text(run.logs_dir / "producer.log", producer_log.stdout + producer_log.stderr)
    producer_end_offset = _kafka_topic_log_end(kafka, config)

    verify = _verify_ts_table(target, run, runner)
    size = _table_data_size(target, run, runner)
    write_json(run.database_dir / "write-verify.json", verify)
    write_json(run.database_dir / "table-size.json", size)
    status = "success"
    error = None
    if config.write.require_zero_lag and final_lag != 0:
        status = "failed"
        error = f"Kafka lag did not reach zero within {config.write.lag_drain_timeout_seconds}s"
    written_rows = _int_or_none(verify.get("written_rows"))
    if status == "success" and (written_rows is None or written_rows <= 0):
        status = "failed"
        error = "ts-write produced zero rows"
    timing = _parse_mxgate_timing_log(run.logs_dir / "mxgate.log", run.target_table)
    if status == "success" and written_rows and not timing.get("mxgate_elapsed_seconds"):
        status = "failed"
        error = "mxgate timing logs were not found for committed rows"
    actual_qps = _actual_qps(written_rows, timing.get("mxgate_elapsed_seconds"))
    produced_messages = max(0, producer_end_offset - producer_start_offset) if producer_end_offset is not None and producer_start_offset is not None else None
    producer_actual_qps = _actual_qps(produced_messages, config.write.duration_seconds[0])
    pressure_profile = _pressure_profile(config)
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": "ts-write",
        "compress_threshold": run.compress_threshold,
        "kafka_topic": run.kafka_topic,
        "target_table": run.target_table,
        "duration_seconds": config.write.duration_seconds[0],
        "pressure_level": config.write.pressure_level,
        "producer_lead_seconds": config.write.producer_lead_seconds,
        "producer_target_qps": _profile_int(pressure_profile, "target_qps", config.write.workers[0] * config.write.rate_per_worker[0]),
        "target_qps": _profile_int(pressure_profile, "target_qps", config.write.workers[0] * config.write.rate_per_worker[0]),
        "actual_qps": actual_qps,
        "producer_actual_qps": producer_actual_qps,
        "produced_messages": produced_messages,
        "producer_ack_qps": None,
        "producer_attempted": _profile_int(pressure_profile, "target_qps", config.write.workers[0] * config.write.rate_per_worker[0]) * config.write.duration_seconds[0],
        "producer_errors": None,
        "written_rows": verify.get("written_rows"),
        **timing,
        "distinct_vins": verify.get("distinct_vins"),
        "min_timestamp": verify.get("min_timestamp"),
        "max_timestamp": verify.get("max_timestamp"),
        "final_lag": final_lag,
        "max_lag": max_lag,
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": status,
        "error": error,
        "session_start": started,
        "session_end": datetime.now().isoformat(),
        "result_dir": str(run.benchmark_dir),
    }


def _run_ts_query(
    root: Path,
    target: Target,
    config: TsConfig,
    run: TsRunSpec,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult],
    ssh_factory: Callable[[str, int, str, str], SSHClient],
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    ready = _ensure_query_dependency(root, target, config, run, config.query.dependency_mode, runner, ssh_factory)
    if ready:
        return ready
    samples = _sample_ranges(target, run, config.query.rounds[0], runner)
    _write_csv(run.benchmark_dir / "input.csv", ["vin", "start_time", "end_time"], samples)
    timings, rows, errors = _execute_range_queries(target, run, samples, config.query.warmup_rounds[0], config.query.rounds[0], runner)
    size = _table_data_size(target, run, runner)
    write_json(run.database_dir / "table-size.json", size)
    _write_csv(run.benchmark_dir / "results.csv", ["elapsed_ms", "rows", "error"], [[str(t), str(r), e] for t, r, e in zip(timings, rows, errors)])
    rows_returned = sum(rows)
    error_count = sum(1 for error in errors if error)
    status = "success"
    error = None
    if error_count:
        status = "failed"
        error = f"ts-query had {error_count} query error(s)"
    elif rows_returned <= 0:
        status = "failed"
        error = "ts-query returned zero rows"
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": "ts-query",
        "compress_threshold": run.compress_threshold,
        "target_table": run.target_table,
        "rounds": config.query.rounds[0],
        "warmup_rounds": config.query.warmup_rounds[0],
        "query_count": len(timings),
        **_latency_summary(timings),
        "rows_returned": rows_returned,
        "errors": error_count,
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": status,
        "error": error,
        "session_start": started,
        "session_end": datetime.now().isoformat(),
        "result_dir": str(run.benchmark_dir),
    }


def _run_point_query(
    root: Path,
    target: Target,
    config: TsConfig,
    run: TsRunSpec,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult],
    ssh_factory: Callable[[str, int, str, str], SSHClient],
) -> dict[str, Any]:
    started = datetime.now().isoformat()
    ready = _ensure_query_dependency(root, target, config, run, config.point_query.dependency_mode, runner, ssh_factory)
    if ready:
        return ready
    samples = _sample_points(target, run, config.point_query.sample_size[0], runner)
    _write_csv(run.benchmark_dir / "input.csv", ["vin", "timestamp"], samples)
    timings, rows, errors = _execute_point_queries(target, run, samples, config.point_query.warmup_rounds[0], config.point_query.rounds[0], runner)
    size = _table_data_size(target, run, runner)
    write_json(run.database_dir / "table-size.json", size)
    _write_csv(run.benchmark_dir / "results.csv", ["elapsed_ms", "hit", "rows", "error"], [[str(t), "1" if r else "0", str(r), e] for t, r, e in zip(timings, rows, errors)])
    hits = sum(1 for row_count in rows if row_count > 0)
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": "point-query",
        "compress_threshold": run.compress_threshold,
        "target_table": run.target_table,
        "sample_size": config.point_query.sample_size[0],
        "rounds": config.point_query.rounds[0],
        "warmup_rounds": config.point_query.warmup_rounds[0],
        "query_count": len(timings),
        **_latency_summary(timings),
        "hit_rate": hits / len(rows) if rows else 0,
        "errors": sum(1 for error in errors if error),
        "table_data_size": size.get("table_data_size"),
        "table_data_size_bytes": size.get("table_data_size_bytes"),
        "status": "success" if not any(errors) else "failed",
        "session_start": started,
        "session_end": datetime.now().isoformat(),
        "result_dir": str(run.benchmark_dir),
    }


def _ensure_query_dependency(
    root: Path,
    target: Target,
    config: TsConfig,
    run: TsRunSpec,
    mode: str,
    runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult],
    ssh_factory: Callable[[str, int, str, str], SSHClient],
) -> dict[str, Any] | None:
    exists = _table_has_data(target, run, runner)
    if exists:
        return None
    if mode == "auto-write":
        write_run = TsRunSpec(
            run_id=run.run_id.replace(f"-{run.stage}", "-ts-write"),
            target_id=run.target_id,
            stage="ts-write",
            compress_threshold=run.compress_threshold,
            target_table=run.target_table,
            kafka_topic=run.kafka_topic,
            work_dir=root / "work" / "ts" / run.run_id.replace(f"-{run.stage}", "-ts-write"),
            run_dir=root / "runs" / run.run_id.replace(f"-{run.stage}", "-ts-write"),
            benchmark_dir=root / "runs" / run.run_id.replace(f"-{run.stage}", "-ts-write") / "benchmark" / "ts-write",
            database_dir=root / "runs" / run.run_id.replace(f"-{run.stage}", "-ts-write") / "database",
            logs_dir=root / "runs" / run.run_id.replace(f"-{run.stage}", "-ts-write") / "logs",
            collector_dir=root / "runs" / run.run_id.replace(f"-{run.stage}", "-ts-write") / "collectors",
            run_mins=run.run_mins,
        )
        write_run.run_dir.mkdir(parents=True, exist_ok=True)
        write_result = _run_ts_write(root, target, config, write_run, runner, ssh_factory)
        write_json(write_run.run_dir / "result.json", write_result)
        if write_result.get("status") == "success":
            return None
        return _failed_ts_result(run, datetime.now().isoformat(), f"auto-write failed for {run.target_table}: {write_result.get('error') or 'unknown error'}")
    return _failed_ts_result(run, datetime.now().isoformat(), f"{run.target_table} is missing or empty; run ts-write first or set dependency_mode: auto-write")


def _table_has_data(target: Target, run: TsRunSpec, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> bool:
    exists = _psql(target, f"select to_regclass('public.{run.target_table}') is not null;", runner, timeout=120)
    if exists.exit_code != 0 or not exists.stdout.strip().lower().endswith("t"):
        return False
    count = _psql(target, f"select exists(select 1 from public.{_ident(run.target_table)} limit 1);", runner, timeout=120)
    return count.exit_code == 0 and count.stdout.strip().lower().endswith("t")


def _verify_ts_table(target: Target, run: TsRunSpec, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> dict[str, Any]:
    query = (
        f"select count(*), count(distinct vin), min(\"timeStamp\"), max(\"timeStamp\") "
        f"from public.{_ident(run.target_table)};"
    )
    result = _psql(target, query, runner, timeout=300)
    if result.exit_code != 0:
        return {"error": result.stderr or result.stdout}
    parts = [part.strip() for part in result.stdout.strip().split("|")]
    return {
        "written_rows": _int_or_none(parts[0] if len(parts) > 0 else None),
        "distinct_vins": _int_or_none(parts[1] if len(parts) > 1 else None),
        "min_timestamp": parts[2] if len(parts) > 2 else None,
        "max_timestamp": parts[3] if len(parts) > 3 else None,
    }


def _table_data_size(target: Target, run: TsRunSpec, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> dict[str, Any]:
    pattern = f"^({re.escape(run.target_table)})$"
    query = f"""
SELECT s.tps
FROM pg_catalog.pg_class c
     LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     LEFT JOIN pg_catalog.pg_inherits inh ON c.oid = inh.inhrelid,
     LATERAL (SELECT pg_catalog.pg_size_pretty(sum(
                 CASE WHEN ppt.isleaf AND ppt.level = 1
                      THEN pg_catalog.pg_table_size(ppt.relid) ELSE 0 END)) AS dps,
                     pg_catalog.pg_size_pretty(sum(pg_catalog.pg_table_size(ppt.relid))) AS tps
              FROM pg_catalog.pg_partition_tree(c.oid) ppt) s
WHERE c.relkind IN ('p','')
      AND n.nspname !~ '^pg_toast'
  AND c.relname OPERATOR(pg_catalog.~) {_sql_literal(pattern)} COLLATE pg_catalog.default
  AND pg_catalog.pg_table_is_visible(c.oid)
ORDER BY n.nspname, inh.inhparent::pg_catalog.regclass NULLS FIRST, c.relname
LIMIT 1;
"""
    result = _psql(target, query, runner, timeout=120)
    size = result.stdout.strip() if result.exit_code == 0 else None
    return {
        "target_table": run.target_table,
        "table_data_size": size or None,
        "table_data_size_bytes": _int_or_none(size),
        "error": None if result.exit_code == 0 else result.stderr or result.stdout,
    }


def _sample_ranges(target: Target, run: TsRunSpec, count: int, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> list[list[str]]:
    query = f"select vin, min(\"timeStamp\"), max(\"timeStamp\") + interval '1 millisecond' from public.{_ident(run.target_table)} group by vin order by random() limit {int(count)};"
    result = _psql(target, query, runner, timeout=300)
    return [line.split("|") for line in result.stdout.splitlines() if line.strip()]


def _sample_points(target: Target, run: TsRunSpec, count: int, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> list[list[str]]:
    query = f"select vin, \"timeStamp\" from public.{_ident(run.target_table)} order by random() limit {int(count)};"
    result = _psql(target, query, runner, timeout=300)
    return [line.split("|") for line in result.stdout.splitlines() if line.strip()]


def _execute_range_queries(target: Target, run: TsRunSpec, samples: list[list[str]], warmups: int, rounds: int, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> tuple[list[float], list[int], list[str]]:
    timings: list[float] = []
    rows: list[int] = []
    errors: list[str] = []
    for index in range(warmups + rounds):
        for vin, start_time, end_time, *_ in samples:
            query = (
                f"select count(*) from public.{_ident(run.target_table)} "
                f"where vin = {_sql_literal(vin)} and \"timeStamp\" >= {_sql_literal(start_time)}::timestamp "
                f"and \"timeStamp\" < {_sql_literal(end_time)}::timestamp;"
            )
            elapsed, row_count, error = _timed_count_query(target, query, runner)
            if index >= warmups:
                timings.append(elapsed)
                rows.append(row_count)
                errors.append(error)
    return timings, rows, errors


def _execute_point_queries(target: Target, run: TsRunSpec, samples: list[list[str]], warmups: int, rounds: int, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> tuple[list[float], list[int], list[str]]:
    timings: list[float] = []
    rows: list[int] = []
    errors: list[str] = []
    for index in range(warmups + rounds):
        for vin, timestamp, *_ in samples:
            query = (
                f"select count(*) from (select * from public.{_ident(run.target_table)} "
                f"where vin = {_sql_literal(vin)} and \"timeStamp\" >= {_sql_literal(timestamp)}::timestamp "
                f"and \"timeStamp\" < {_sql_literal(timestamp)}::timestamp + interval '10 seconds' "
                f"order by \"timeStamp\" limit 1) s;"
            )
            elapsed, row_count, error = _timed_count_query(target, query, runner)
            if index >= warmups:
                timings.append(elapsed)
                rows.append(row_count)
                errors.append(error)
    return timings, rows, errors


def _timed_count_query(target: Target, query: str, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult]) -> tuple[float, int, str]:
    start = time.perf_counter()
    result = _psql(target, query, runner, timeout=7200)
    elapsed = (time.perf_counter() - start) * 1000
    if result.exit_code != 0:
        return elapsed, 0, result.stderr or result.stdout
    return elapsed, _int_or_none(result.stdout.strip()) or 0, ""


def _latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
    ordered = sorted(values)
    return {
        "avg_ms": sum(values) / len(values),
        "p50_ms": _percentile(ordered, 50),
        "p95_ms": _percentile(ordered, 95),
        "p99_ms": _percentile(ordered, 99),
    }


def _percentile(ordered: list[float], percentile: int) -> float:
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
    return ordered[index]


def _psql(target: Target, sql: str, runner: Callable[[list[str], Path, int, dict[str, str] | None], CommandResult], timeout: int) -> CommandResult:
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
        "-tAc",
        sql,
    ]
    return runner(command, Path.cwd(), timeout, {"PGPASSWORD": target.connection.db_password})


def _run_local(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> CommandResult:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False, env=run_env)
        return CommandResult(" ".join(command), completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(" ".join(command), 124, _ensure_text(exc.stdout), _ensure_text(exc.stderr) or f"command timed out after {timeout} seconds")


def _failed_ts_result(run: TsRunSpec, started: str, error: str) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "compress_threshold": run.compress_threshold,
        "target_table": run.target_table,
        "kafka_topic": run.kafka_topic,
        "status": "failed",
        "error": error.strip(),
        "session_start": started,
        "session_end": datetime.now().isoformat(),
    }


def _ts_table_ddl(table: str, threshold: int) -> str:
    return f"""
drop table if exists public.{_ident(table)};
create table public.{_ident(table)} (
  "timeStamp" timestamp not null,
  vin text not null,
  speed double precision,
  mileage double precision,
  raw_payload text
)
using mars3
with(
  mars3options='prefer_load_mode=bulk,rowstore_size=64',
  compresstype=zstd,
  compresslevel=2,
  compress_threshold={int(threshold)}
)
distributed by (vin)
order by (vin,"timeStamp")
partition by range ("timeStamp")
(
  start('2026-01-01 00:00:00'::timestamp) inclusive
  end('2027-01-01 00:00:00'::timestamp) exclusive
  every(interval '1 day'),
  default partition others
);
"""


def _write_mxgate_conf(path: Path, target: Target, config: TsConfig, run: TsRunSpec) -> None:
    job_name = _mxgate_job_name(run)
    consumer_group = _mxgate_consumer_group(run)
    kafka_broker = _mxgate_kafka_broker(config.kafka)
    lines = [
        "[database]",
        f'  db-database = "{_toml_string(target.connection.db_name)}"',
        f'  db-master-host = "{_toml_string(target.connection.db_host)}"',
        f"  db-master-port = {int(target.connection.db_port)}",
        f'  db-password = "{_toml_string(target.connection.db_password)}"',
        f'  db-user = "{_toml_string(target.connection.db_user)}"',
        "",
        "[job]",
        f'  delimiter = "{_toml_string(config.mxgate.delimiter)}"',
        f'  format = "{_toml_string(config.mxgate.format)}"',
        f'  time-format = "{_toml_string(config.mxgate.time_format)}"',
        "",
        "  [[job.target]]",
        f'    name = "{_toml_string(job_name)}"',
        '    schema = "public"',
        f'    table = "{_toml_string(run.target_table)}"',
        "",
        "[source]",
        f'  source = "{_toml_string(config.mxgate.source)}"',
        "",
        "  [source.kafka]",
        f'    kafka-broker = "{_toml_string(kafka_broker)}"',
        "",
        "    [[source.kafka.topic]]",
        f'      consumer-group = "{_toml_string(consumer_group)}"',
        f'      job = "{_toml_string(job_name)}"',
        '      partition = "*"',
        f'      topic = "{_toml_string(run.kafka_topic)}"',
        "",
        "[transform]",
        f'  transform = "{_toml_string(config.mxgate.transform)}"',
        "",
        "[writer]",
        f"  bytes-limit = {int(config.mxgate.bytes_limit)}",
        f"  interval = {int(config.mxgate.interval_ms)}",
        f'  writer = "{_toml_string(config.mxgate.writer)}"',
        "",
        "  [writer.stream]",
        f"    compress-pool-size = {int(config.mxgate.compress_pool_size)}",
        f"    enable-event-trigger = {_toml_bool(config.mxgate.enable_event_trigger)}",
        f"    insert-timeout = {int(config.mxgate.insert_timeout)}",
        f"    max-seg-conn = {int(config.mxgate.max_seg_conn)}",
        f"    seg-conn-timeout-millis = {int(config.mxgate.seg_conn_timeout_millis)}",
        f"    stream-prepared = {int(config.mxgate.stream_prepared)}",
        "    timing = true",
        f'    use-gzip = "{_toml_string(config.mxgate.stream_use_gzip)}"',
        f"    write-buffer-size = {int(config.mxgate.write_buffer_size)}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_string(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _mxgate_job_name(run: TsRunSpec) -> str:
    return f"job_text_to_public.{run.target_table}"


def _mxgate_consumer_group(run: TsRunSpec) -> str:
    return f"{run.kafka_topic}_public.{run.target_table}"


def _mxgate_kafka_broker(config: KafkaConfig) -> str:
    brokers: list[str] = []
    for raw_broker in config.bootstrap_server.split(","):
        broker = raw_broker.strip()
        if not broker:
            continue
        host, sep, port = broker.rpartition(":")
        if sep and host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
            brokers.append(f"{config.host}:{port}")
        elif broker in {"localhost", "127.0.0.1", "::1", "[::1]"}:
            brokers.append(config.host)
        else:
            brokers.append(broker)
    return ",".join(brokers) or config.bootstrap_server


def _producer_start_command(config: TsConfig, run: TsRunSpec) -> str:
    profile = _pressure_profile(config)
    if str(profile.get("producer_type", "kafka_perf")) == "kafka_perf":
        return _kafka_perf_producer_start_command(config, run, profile)
    return _shell_producer_start_command(config, run)


def _kafka_perf_producer_start_command(config: TsConfig, run: TsRunSpec, profile: dict[str, object]) -> str:
    duration = int(config.write.duration_seconds[0])
    target_qps = _profile_int(profile, "target_qps", config.write.workers[0] * config.write.rate_per_worker[0])
    producers = max(1, _profile_int(profile, "producers", 1))
    per_producer_records = max(1, (target_qps * duration + producers - 1) // producers)
    per_producer_throughput = max(1, target_qps // producers)
    batch_size = _profile_int(profile, "batch_size", 10000)
    linger_ms = _profile_int(profile, "linger_ms", 5)
    payload_bytes = _profile_int(profile, "payload_bytes", 128)
    sample_records = min(max(1, _profile_int(profile, "sample_records", 10000)), max(1, config.write.vins[0]))
    perf = f"{config.kafka.kafka_bin.rstrip('/')}/kafka-producer-perf-test.sh"
    payload_file = f"/tmp/automan-ts-{run.run_id}.payload"
    log_prefix = f"/tmp/automan-ts-{run.run_id}.producer.log"
    script = f"""
set -eu
payload_file={shlex.quote(payload_file)}
log_prefix={shlex.quote(log_prefix)}
rm -f "$payload_file" "$log_prefix".*
base_ts=$(date +%s%3N)
i=0
pad=$(printf '%*s' {payload_bytes} '' | tr ' ' 'x')
while [ "$i" -lt {sample_records} ]; do
  vin=$(printf 'VIN%08d' $(( i % {max(1, config.write.vins[0])} )))
  ts=$(( base_ts + (i % {max(1, duration * 1000)}) ))
  printf '%s|%s|%s|%s|payload-%s-%s\\n' "$ts" "$vin" "$(( i % 220 ))" "$i" "$i" "$pad"
  i=$(( i + 1 ))
done > "$payload_file"
pids=""
idx=0
while [ "$idx" -lt {producers} ]; do
  nohup {shlex.quote(perf)} --topic {shlex.quote(config.kafka.topic)} \\
    --num-records {per_producer_records} \\
    --throughput {per_producer_throughput} \\
    --payload-file "$payload_file" \\
    --producer-props bootstrap.servers={shlex.quote(config.kafka.bootstrap_server)} batch.size={batch_size} linger.ms={linger_ms} acks=1 client.id=automan-{shlex.quote(run.run_id)}-$idx \\
    > "$log_prefix.$idx" 2>&1 < /dev/null &
  pids="$pids $!"
  idx=$(( idx + 1 ))
done
echo "$pids"
"""
    return f"sh -lc {shlex.quote(script)}"


def _shell_producer_start_command(config: TsConfig, run: TsRunSpec) -> str:
    duration = int(config.write.duration_seconds[0])
    workers = int(config.write.workers[0])
    rate = int(config.write.rate_per_worker[0])
    vins = int(config.write.vins[0])
    rows_per_tick = max(1, workers * rate)
    batch_chunk = min(rows_per_tick, 5000)
    producer = f"{config.kafka.kafka_bin.rstrip('/')}/kafka-console-producer.sh"
    log = f"/tmp/automan-ts-{run.run_id}.producer.log"
    script = f"""
end=$(( $(date +%s) + {duration} ))
i=0
while [ "$(date +%s)" -lt "$end" ]; do
  batch=0
  while [ "$batch" -lt {rows_per_tick} ] && [ "$(date +%s)" -lt "$end" ]; do
    chunk=0
    while [ "$chunk" -lt {batch_chunk} ] && [ "$batch" -lt {rows_per_tick} ]; do
      vin=$(printf 'VIN%08d' $(( i % {max(1, vins)} )))
      ts=$(date +%s%3N)
      printf '%s|%s|%s|%s|payload-%s\\n' "$ts" "$vin" "$(( i % 220 ))" "$i" "$i"
      i=$(( i + 1 ))
      batch=$(( batch + 1 ))
      chunk=$(( chunk + 1 ))
    done
  done
  sleep 1
done | {shlex.quote(producer)} --bootstrap-server {shlex.quote(config.kafka.bootstrap_server)} --topic {shlex.quote(config.kafka.topic)}
"""
    return f"nohup sh -lc {shlex.quote(script)} > {shlex.quote(log)} 2>&1 < /dev/null & echo $!"


def _mxgate_start_command(config: TsConfig, remote_conf: str, run: TsRunSpec) -> str:
    workdir = f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}"
    binary = shlex.quote(config.mxgate.binary)
    pid_file = _mxgate_pid_file(config, run)
    script = f"""
set -u
cd {shlex.quote(workdir)}
rm -f {shlex.quote(pid_file)}
{binary} start --config {shlex.quote(remote_conf)} > mxgate-start.log 2>&1
rc=$?
cat mxgate-start.log
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
conf_abs=$(readlink -f {shlex.quote(remote_conf)})
pid=""
for lock in /tmp/.s.MXGATED.*.lock; do
  [ -f "$lock" ] || continue
  lock_pid=$(sed -n '1p' "$lock")
  lock_conf=$(sed -n '6p' "$lock")
  [ -n "$lock_pid" ] || continue
  [ -n "$lock_conf" ] || continue
  if [ "$(readlink -f "$lock_conf" 2>/dev/null || printf '%s' "$lock_conf")" = "$conf_abs" ]; then
    pid="$lock_pid"
    break
  fi
done
if [ -z "$pid" ]; then
  echo "AUTOMAN_MXGATE_PID_NOT_FOUND for $conf_abs" >&2
  exit 3
fi
printf '%s\\n' "$pid" > {shlex.quote(pid_file)}
echo "AUTOMAN_MXGATE_PID=$pid"
"""
    return f"sh -lc {shlex.quote(script)}"


def _extract_mxgate_pid(output: str) -> str | None:
    match = re.search(r"^AUTOMAN_MXGATE_PID=(\d+)\s*$", output, re.MULTILINE)
    return match.group(1) if match else None


def _mxgate_status_command(config: TsConfig, run: TsRunSpec) -> str:
    workdir = f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}"
    return _mxgate_pid_command(config, run, f"{shlex.quote(config.mxgate.binary)} status --pid \"$pid\"")


def _mxgate_log_command(config: TsConfig, run: TsRunSpec) -> str:
    return _mxgate_pid_command(config, run, f"{shlex.quote(config.mxgate.binary)} log --pid \"$pid\" --lines 1000000")


def _mxgate_stop_command(config: TsConfig, run: TsRunSpec) -> str:
    return _mxgate_pid_command(config, run, f"{shlex.quote(config.mxgate.binary)} stop --pid \"$pid\"")


def _mxgate_pid_file(config: TsConfig, run: TsRunSpec) -> str:
    return f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}/mxgate.pid"


def _mxgate_pid_command(config: TsConfig, run: TsRunSpec, action: str) -> str:
    workdir = f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}"
    pid_file = _mxgate_pid_file(config, run)
    remote_conf = f"{config.mxgate.workdir.rstrip('/')}/work/ts/{run.run_id}/mxgate.conf"
    guard = f"""
set -eu
cd {shlex.quote(workdir)}
pid=$(cat {shlex.quote(pid_file)})
conf_abs=$(readlink -f {shlex.quote(remote_conf)})
found=""
for lock in /tmp/.s.MXGATED.*.lock; do
  [ -f "$lock" ] || continue
  lock_pid=$(sed -n '1p' "$lock")
  lock_conf=$(sed -n '6p' "$lock")
  if [ "$lock_pid" = "$pid" ] && [ "$(readlink -f "$lock_conf" 2>/dev/null || printf '%s' "$lock_conf")" = "$conf_abs" ]; then
    found="$lock"
    break
  fi
done
if [ -z "$found" ]; then
  echo "Refuse to operate mxgate pid $pid: lock file does not match $conf_abs" >&2
  exit 4
fi
{action}
"""
    return f"sh -lc {shlex.quote(guard)}"


def _kafka_lag(client: SSHClient, config: TsConfig, run: TsRunSpec) -> int:
    consumer_groups = f"{config.kafka.kafka_bin.rstrip('/')}/kafka-consumer-groups.sh"
    command = (
        f"{shlex.quote(consumer_groups)} --bootstrap-server {shlex.quote(config.kafka.bootstrap_server)} "
        f"--group {shlex.quote(_mxgate_consumer_group(run))} --describe 2>/dev/null "
        f"| awk -v topic={shlex.quote(run.kafka_topic)} '$2 == topic {{sum += $6}} END {{print sum+0}}'"
    )
    result = client.run(command, 120)
    return _int_or_none(result.stdout.strip()) or 0


def _kafka_topic_log_end(client: SSHClient, config: TsConfig) -> int | None:
    offsets = f"{config.kafka.kafka_bin.rstrip('/')}/kafka-get-offsets.sh"
    command = (
        f"{shlex.quote(offsets)} --bootstrap-server {shlex.quote(config.kafka.bootstrap_server)} "
        f"--topic {shlex.quote(config.kafka.topic)} 2>/dev/null "
        "| awk -F: '{sum += $3} END {print sum+0}'"
    )
    result = client.run(command, 120)
    return _int_or_none(result.stdout.strip()) if result.exit_code == 0 else None


def _remote_write_file_command(path: str, content: str) -> str:
    return f"mkdir -p {shlex.quote(posixpath.dirname(path))} && cat > {shlex.quote(path)} <<'AUTOMAN_TS_FILE'\n{content}AUTOMAN_TS_FILE\n"


def _stop_process_command(pid: str) -> str:
    if not pid:
        return "true"
    return f"pids={shlex.quote(pid)}; for pid in $pids; do case \"$pid\" in *[!0-9]*|'') ;; *) kill -TERM \"$pid\" 2>/dev/null || true ;; esac; done"


def _write_command_result(logs_dir: Path, phase: str, result: CommandResult) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    _write_text(logs_dir / f"{phase}.stdout.log", result.stdout)
    _write_text(logs_dir / f"{phase}.stderr.log", result.stderr)
    write_json(logs_dir / f"{phase}.result.json", {"phase": phase, "command": result.command, "exit_code": result.exit_code})


def _collector_manager(root: Path, target: Target, run: TsRunSpec, collectors: CollectorConfig | dict | None) -> CollectorManager | NullCollectorManager:
    config = _collector_config_dict(root, collectors)
    if not config.get("enabled", True):
        return NullCollectorManager()
    return CollectorManager(root, target, run, config=config)


def _collector_config_dict(root: Path, collectors: CollectorConfig | dict | None) -> dict[str, Any]:
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


def _set_job_status(job_dir: Path, status: str, last_error: str | None = None) -> None:
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


def _set_run_status(run_dir: Path, status: str, phase: str | None, last_error: str | None = None) -> None:
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


def _update_job_state(job_dir: Path, target_id: str, status: str, current_run: str | None, current_phase: str | None) -> None:
    state = load_yaml(job_dir / "job.json")
    for target in state.get("targets", []):
        if target.get("target_id") == target_id:
            target["status"] = status
            target["current_run"] = current_run
            target["current_phase"] = current_phase
    state["running_runs"] = sum(1 for target in state.get("targets", []) if target.get("current_run"))
    write_json(job_dir / "job.json", state)


def _mark_run_finished(job_dir: Path, target_id: str, failed: bool, last_error: str | None = None) -> None:
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


def _run_dict(run: TsRunSpec) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "compress_threshold": run.compress_threshold,
        "target_table": run.target_table,
        "kafka_topic": run.kafka_topic,
        "work_dir": str(run.work_dir),
        "run_dir": str(run.run_dir),
        "resolved_task_path": str(run.run_dir / "resolved-task.yaml"),
        "status_path": str(run.run_dir / "status.json"),
        "command_log_dir": str(run.logs_dir),
        "benchmark_parent_dir": str(run.run_dir / "benchmark"),
        "benchmark_result_dir": str(run.benchmark_dir),
        "database_dir": str(run.database_dir),
        "collector_dir": str(run.collector_dir),
    }


def _redacted_kafka(config: KafkaConfig) -> dict[str, Any]:
    data = asdict(config)
    data["password"] = "***" if data.get("password") else ""
    return data


def _redacted_mxgate(config: MxgateConfig) -> dict[str, Any]:
    data = asdict(config)
    data["password"] = "***" if data.get("password") else ""
    return data


def _load_kafka_config(raw: dict[str, Any]) -> KafkaConfig:
    return KafkaConfig(
        host=str(raw.get("host", "")),
        port=int(raw.get("port", 22)),
        user=str(raw.get("user", "")),
        password=str(raw.get("password", "")),
        kafka_bin=str(raw.get("kafka_bin", "")),
        bootstrap_server=str(raw.get("bootstrap_server", "")),
        topic=str(raw.get("topic", "")),
        partitions=_int_list(raw.get("partitions", []), "kafka.partitions"),
        replication_factor=int(raw.get("replication_factor", 1)),
        recreate_topic=_bool(raw.get("recreate_topic", True)),
    )


def _load_mxgate_config(raw: dict[str, Any]) -> MxgateConfig:
    return MxgateConfig(
        user=str(raw.get("user", "mxadmin")),
        password=str(raw.get("password", "")),
        workdir=str(raw.get("workdir", "/home/mxadmin/automan")),
        binary=str(raw.get("binary", "mxgate")),
        source=str(raw.get("source", "kafka")),
        transform=str(raw.get("transform", "plain")),
        writer=str(raw.get("writer", "stream")),
        delimiter=str(raw.get("delimiter", "|")),
        format=str(raw.get("format", "text")),
        time_format=str(raw.get("time_format", "unix-ms")),
        stream_use_gzip=str(raw.get("stream_use_gzip", "no")),
        interval_ms=int(raw.get("interval_ms", 50)),
        bytes_limit=int(raw.get("bytes_limit", 67108864)),
        stream_prepared=int(raw.get("stream_prepared", 36)),
        write_buffer_size=int(raw.get("write_buffer_size", 1048576)),
        compress_pool_size=int(raw.get("compress_pool_size", 4096)),
        max_seg_conn=int(raw.get("max_seg_conn", 128)),
        seg_conn_timeout_millis=int(raw.get("seg_conn_timeout_millis", 30000)),
        insert_timeout=int(raw.get("insert_timeout", 0)),
        enable_event_trigger=_bool(raw.get("enable_event_trigger", False)),
    )


def _load_ts_write_config(raw: dict[str, Any]) -> TsWriteConfig:
    pressure_profiles = _load_pressure_profiles(_mapping(raw.get("pressure_profiles", {}), "ts_write.pressure_profiles"))
    return TsWriteConfig(
        table=str(raw.get("table", "iot_vehicle_raw")),
        workers=_int_list(raw.get("workers", []), "ts_write.workers"),
        rate_per_worker=_int_list(raw.get("rate_per_worker", []), "ts_write.rate_per_worker"),
        vins=_int_list(raw.get("vins", []), "ts_write.vins"),
        vin_interval=_str_list(raw.get("vin_interval", []), "ts_write.vin_interval"),
        duration_seconds=_int_list(raw.get("duration_seconds", []), "ts_write.duration_seconds"),
        pressure_level=str(raw.get("pressure_level", "medium")),
        pressure_profiles=pressure_profiles,
        producer_fast_mode=_bool(raw.get("producer_fast_mode", True)),
        producer_lead_seconds=int(raw.get("producer_lead_seconds", 20)),
        monitor_interval_seconds=int(raw.get("monitor_interval_seconds", 5)),
        lag_drain_timeout_seconds=int(raw.get("lag_drain_timeout_seconds", 600)),
        require_zero_lag=_bool(raw.get("require_zero_lag", True)),
    )


def _load_pressure_profiles(raw: dict[str, Any]) -> dict[str, dict[str, object]]:
    profiles = {name: dict(profile) for name, profile in DEFAULT_PRESSURE_PROFILES.items()}
    for name, value in raw.items():
        if isinstance(value, dict):
            profiles[str(name)] = {**profiles.get(str(name), {}), **value}
    return profiles


def _load_ts_query_config(raw: dict[str, Any]) -> TsQueryConfig:
    return TsQueryConfig(
        dependency_mode=str(raw.get("dependency_mode", "reuse")),
        rounds=_int_list(raw.get("rounds", []), "ts_query.rounds"),
        warmup_rounds=_int_list(raw.get("warmup_rounds", []), "ts_query.warmup_rounds"),
        timeout_seconds=int(raw.get("timeout_seconds", 7200)),
    )


def _load_point_query_config(raw: dict[str, Any]) -> PointQueryConfig:
    return PointQueryConfig(
        dependency_mode=str(raw.get("dependency_mode", "reuse")),
        rounds=_int_list(raw.get("rounds", []), "point_query.rounds"),
        warmup_rounds=_int_list(raw.get("warmup_rounds", []), "point_query.warmup_rounds"),
        sample_size=_int_list(raw.get("sample_size", []), "point_query.sample_size"),
        timeout_seconds=int(raw.get("timeout_seconds", 7200)),
    )


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


def _ident(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"unsafe SQL identifier: {value}")
    return value


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


_MXGATE_TIMING_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}:\d{2}:\d{2}:\d{2}\.\d+).*?"
    r"\[Writer\.Stream\] Insert (?P<rows>\d+) rows to (?P<table>\S+).*?"
    r" timing \((?P<durations>[^)]*)\)"
)
_DURATION_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>ns|us|µs|μs|ms|s|m|h)")
_DURATION_MULTIPLIER = {
    "ns": 0.000000001,
    "us": 0.000001,
    "µs": 0.000001,
    "μs": 0.000001,
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}


def _parse_mxgate_timing_log(log_path: Path, target_table: str) -> dict[str, Any]:
    if not log_path.exists():
        return {}
    start_time: datetime | None = None
    end_time: datetime | None = None
    timing_rows = 0
    positive_batches = 0
    zero_batches = 0
    total_batches = 0
    table_suffix = f".{target_table}"
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _MXGATE_TIMING_RE.search(line)
        if not match:
            continue
        table = match.group("table")
        if table != target_table and not table.endswith(table_suffix):
            continue
        rows = int(match.group("rows"))
        total_batches += 1
        if rows <= 0:
            zero_batches += 1
            continue
        durations = [_duration_seconds(token) for token in match.group("durations").split()]
        if not durations:
            continue
        batch_end = datetime.strptime(match.group("ts"), "%Y-%m-%d:%H:%M:%S.%f")
        batch_start_dt = batch_end - timedelta(seconds=sum(durations))
        start_time = batch_start_dt if start_time is None or batch_start_dt < start_time else start_time
        end_time = batch_end if end_time is None or batch_end > end_time else end_time
        timing_rows += rows
        positive_batches += 1
    if start_time is None or end_time is None:
        return {
            "mxgate_timing_batches": total_batches,
            "mxgate_timing_positive_batches": positive_batches,
            "mxgate_timing_zero_rows_batches": zero_batches,
            "mxgate_timing_rows": timing_rows,
        }
    elapsed = (end_time - start_time).total_seconds()
    return {
        "mxgate_write_start": start_time.isoformat(timespec="milliseconds"),
        "mxgate_write_end": end_time.isoformat(timespec="milliseconds"),
        "mxgate_elapsed_seconds": round(elapsed, 6),
        "mxgate_timing_batches": total_batches,
        "mxgate_timing_positive_batches": positive_batches,
        "mxgate_timing_zero_rows_batches": zero_batches,
        "mxgate_timing_rows": timing_rows,
    }


def _duration_seconds(value: str) -> float:
    normalized = value.replace(chr(181), "µ").replace(chr(956), "μ")
    total = 0.0
    matched = False
    for match in _DURATION_RE.finditer(normalized):
        matched = True
        total += float(match.group("value")) * _DURATION_MULTIPLIER[match.group("unit")]
    if not matched:
        raise ValueError(f"unsupported mxgate timing duration: {value}")
    return total


def _actual_qps(written_rows: int | None, duration_seconds: int | float | None) -> float | None:
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        return None
    if written_rows is None or duration <= 0:
        return None
    return round(float(written_rows) / duration, 2)


def _pressure_profile(config: TsConfig) -> dict[str, object]:
    profile = config.write.pressure_profiles.get(config.write.pressure_level, {})
    return profile if isinstance(profile, dict) else {}


def _profile_int(profile: dict[str, object], key: str, default: int) -> int:
    try:
        return int(profile.get(key, default))
    except (TypeError, ValueError):
        return default


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def _append_csv(path: Path, header: list[str], row: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)


def _ensure_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
