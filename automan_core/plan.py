from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.models import RunSpec, Target, TpccMatrix


DEFAULT_BENCHMARKSQL_PROPERTIES: dict[str, Any] = {
    "useWarehouseFrom": -1,
    "useWarehouseTo": -1,
    "monkeys": 1,
    "sutThreads": 4,
    "maxDeliveryBGThreads": 4,
    "maxDeliveryBGPerWarehouse": 1,
    "rampupMins": 5,
    "rampupSUTMins": 1,
    "rampupTerminalMins": 1,
    "reportIntervalSecs": 10,
    "resultIntervalSecs": 10,
    "restartSUTThreadProbability": 0.0,
    "keyingTimeMultiplier": 1.0,
    "thinkTimeMultiplier": 1.0,
    "terminalMultiplier": 1,
    "traceTerminalIO": False,
    "runTxnsPerTerminal": 0,
    "limitTxnsPerMin": 0,
    "terminalWarehouseFixed": False,
    "useStoredProcedures": False,
    "paymentWeight": 43.2,
    "orderStatusWeight": 4.2,
    "deliveryWeight": 4.2,
    "stockLevelWeight": 4.2,
    "newOrderWeight": "44.200",
    "rollbackPercent": 1.01,
}

TEMPLATE_BENCHMARKSQL_KEYS = set(DEFAULT_BENCHMARKSQL_PROPERTIES)
RESERVED_BENCHMARKSQL_KEYS = {
    "db",
    "driver",
    "conn",
    "user",
    "password",
    "warehouses",
    "loadWorkers",
    "terminals",
    "runMins",
    "resultDirectory",
}


def new_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-tpcc")


def build_run_specs(root: Path, job_id: str, targets: list[Target], matrix: TpccMatrix) -> list[RunSpec]:
    specs: list[RunSpec] = []
    for target in targets:
        is_first_target_run = True
        for warehouse in matrix.warehouses:
            for terminals in matrix.terminals:
                run_id = f"{job_id}-{target.id}-w{warehouse}-c{terminals}"
                work_dir = root / "work" / "tpcc" / "benchmarksql" / run_id
                properties_path = work_dir / "tpcc.properties"
                specs.append(
                    RunSpec(
                        run_id=run_id,
                        target_id=target.id,
                        warehouse=warehouse,
                        terminals=terminals,
                        load_workers=matrix.load_workers,
                        run_mins=matrix.run_mins,
                        ddl_profile=target.profile.ddl_profile,
                        ddl_dir=target.profile.ddl_dir,
                        properties_path=properties_path,
                        work_dir=work_dir,
                        benchmark_run_dir=work_dir / "benchmarksql" / "run",
                        skip_destroy=is_first_target_run,
                    )
                )
                is_first_target_run = False
    return specs


def render_properties(root: Path, target: Target, run: RunSpec) -> str:
    template_path = root / "benchmarks" / "tpcc" / "benchmarksql" / "props.template"
    text = template_path.read_text(encoding="utf-8")
    jdbc_url = f"jdbc:postgresql://{target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}"
    benchmarksql_properties = _benchmarksql_properties(root)
    values = {
        **{key: _property_value(value) for key, value in benchmarksql_properties.items()},
        "jdbc_url": jdbc_url,
        "user": target.connection.db_user,
        "password": target.connection.db_password,
        "warehouses": str(run.warehouse),
        "load_workers": str(run.load_workers),
        "terminals": str(run.terminals),
        "run_minutes": str(run.run_mins),
        "result_directory": f"{root / 'runs' / run.run_id / 'benchmark' / 'result'}",
        "benchmarksql_extra_properties": _extra_benchmarksql_properties(benchmarksql_properties),
    }
    return Template(text).safe_substitute(values)


def _benchmarksql_properties(root: Path) -> dict[str, Any]:
    config_path = root / "configs" / "benchmarks" / "benchmarksql.yaml"
    if not config_path.exists():
        return dict(DEFAULT_BENCHMARKSQL_PROPERTIES)
    loaded = load_yaml(config_path)
    raw = loaded.get("benchmarksql", {})
    if not isinstance(raw, dict):
        return dict(DEFAULT_BENCHMARKSQL_PROPERTIES)
    configured = raw.get("properties", {})
    if not isinstance(configured, dict):
        configured = {}
    return {**DEFAULT_BENCHMARKSQL_PROPERTIES, **configured}


def _extra_benchmarksql_properties(properties: dict[str, Any]) -> str:
    lines = []
    for key, value in properties.items():
        if key in TEMPLATE_BENCHMARKSQL_KEYS or key in RESERVED_BENCHMARKSQL_KEYS:
            continue
        lines.append(f"{key}={_property_value(value)}")
    return "\n".join(lines)


def _property_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def write_job_files(
    root: Path,
    job_id: str,
    targets: list[Target],
    matrix: TpccMatrix,
    runs: list[RunSpec],
    mars3_options: dict[str, Any],
    collectors: dict[str, Any] | None = None,
    stage: str | None = None,
) -> Path:
    job_dir = root / "runs" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    target_map = {target.id: target for target in targets}
    plan = {
        "job_id": job_id,
        "benchmark": "tpcc",
        "stage": stage or "full",
        "targets": [
            {
                "id": target.id,
                "display_name": target.profile.display_name,
                "database_type": target.profile.database_type,
                "storage_engine": target.profile.storage_engine,
                "test_mode": target.profile.test_mode,
                "connection": target.connection.redacted(),
                "host_facts": target.host_facts,
                "recommended_params": target.recommended_params,
                "accepted_params": target.accepted_params,
                "apply_params": False,
                "parameter_application": "manual_only",
                "manual_parameter_commands": target.manual_parameter_commands,
                "mars3_options": target.mars3_options,
                "ddl_profile": target.profile.ddl_profile,
                "ddl_dir": target.profile.ddl_dir,
            }
            for target in targets
        ],
        "matrix": {
            "warehouses": matrix.warehouses,
            "terminals": matrix.terminals,
            "load_workers": matrix.load_workers,
            "run_mins": matrix.run_mins,
        },
        "mars3_options": mars3_options,
        "collectors": collectors or {},
        "runs": [
            {
                "run_id": run.run_id,
                "target_id": run.target_id,
                "warehouse": run.warehouse,
                "terminals": run.terminals,
                "load_workers": run.load_workers,
                "run_mins": run.run_mins,
                "ddl_profile": run.ddl_profile,
                "ddl_dir": run.ddl_dir,
                "properties_path": str(run.properties_path),
                "work_dir": str(run.work_dir),
                "benchmark_run_dir": str(run.benchmark_run_dir),
                "run_dir": str(root / "runs" / run.run_id),
                "resolved_task_path": str(root / "runs" / run.run_id / "resolved-task.yaml"),
                "status_path": str(root / "runs" / run.run_id / "status.json"),
                "command_log_dir": str(root / "runs" / run.run_id / "logs"),
                "benchmark_parent_dir": str(root / "runs" / run.run_id / "benchmark"),
                "benchmark_result_dir": str(root / "runs" / run.run_id / "benchmark" / "result"),
                "collector_dir": str(root / "runs" / run.run_id / "collectors"),
                "skip_destroy": run.skip_destroy,
            }
            for run in runs
        ],
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
        "scheduling": {
            "different_hosts": "parallel",
            "same_host": "serial_by_selection_order",
        },
        "per_run_sequence": [
            *(
                {
                    None: ["runDatabaseDestroy.sh", "runDatabaseBuild.sh", "runBenchmark.sh"],
                    "destroy": ["runDatabaseDestroy.sh"],
                    "load": ["runDatabaseBuild.sh"],
                    "bench": ["runBenchmark.sh"],
                }[stage]
            ),
        ],
        "destroy_policy": "schema_probe_then_destroy_if_needed" if stage is None else "stage_selected_no_implicit_destroy",
    }
    write_yaml(job_dir / "job.yaml", plan)
    write_yaml(job_dir / "resolved-plan.yaml", plan)

    job_state = {
        "job_id": job_id,
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
    }
    write_json(job_dir / "job.json", job_state)
    write_json(job_dir / "status.json", {"job_id": job_id, "status": "planned"})

    for run in runs:
        target = target_map[run.target_id]
        run_dir = root / "runs" / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run.properties_path.parent.mkdir(parents=True, exist_ok=True)
        run.properties_path.write_text(render_properties(root, target, run), encoding="utf-8")
        write_yaml(
            run_dir / "resolved-task.yaml",
            {
                "run_id": run.run_id,
                "target_id": run.target_id,
                "warehouse": run.warehouse,
                "terminals": run.terminals,
                "load_workers": run.load_workers,
                "run_mins": run.run_mins,
                "ddl_profile": run.ddl_profile,
                "ddl_dir": run.ddl_dir,
                "benchmarksql_properties": str(run.properties_path),
                "work_dir": str(run.work_dir),
                "benchmark_run_dir": str(run.benchmark_run_dir),
                "run_dir": str(run_dir),
                "status_path": str(run_dir / "status.json"),
                "command_log_dir": str(run_dir / "logs"),
                "benchmark_parent_dir": str(run_dir / "benchmark"),
                "benchmark_result_dir": str(run_dir / "benchmark" / "result"),
                "collector_dir": str(run_dir / "collectors"),
                "skip_destroy": run.skip_destroy,
                "command_sequence": [
                    *(
                        _run_command_sequence(run, stage)
                    ),
                ],
            },
        )
        write_json(run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})
    return job_dir


def _run_command_sequence(run: RunSpec, stage: str | None) -> list[str]:
    if stage == "destroy":
        return ["runDatabaseDestroy.sh"]
    if stage == "load":
        return ["runDatabaseBuild.sh"]
    if stage == "bench":
        return ["runBenchmark.sh"]
    return [
        *(["schema_probe", "runDatabaseDestroy.sh when bmsql_* exists"] if run.skip_destroy else ["runDatabaseDestroy.sh"]),
        "runDatabaseBuild.sh",
        "runBenchmark.sh",
    ]
