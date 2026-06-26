from __future__ import annotations

from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.executor import execute_campaign
from automan_core.models import ConnectionInfo, Target, TpccMatrix
from automan_core.parameter_commands import build_manual_parameter_commands
from automan_core.plan import build_run_specs, new_campaign_id, write_campaign_files
from automan_core.profiles import load_database_profiles


def run_task_campaign(root: Path, task_path: Path, plan_only: bool) -> Path:
    task = load_yaml(task_path)
    if task.get("benchmark") != "tpcc":
        raise ValueError("only benchmark: tpcc is supported in this phase")

    profiles = load_database_profiles(root)
    execution = task.get("execution", {})
    matrix = _load_matrix(task.get("matrix", {}))
    targets = [_load_target(raw, profiles, execution) for raw in task.get("targets", [])]
    if not targets:
        raise ValueError("task must define at least one target")

    campaign_id = str(task.get("campaign_id") or new_campaign_id())
    runs = build_run_specs(root, campaign_id, targets, matrix)
    campaign_dir = write_campaign_files(root, campaign_id, targets, matrix, runs, _merged_mars3_options(targets))
    _print_task_plan(campaign_dir, targets, matrix, runs)

    if not plan_only:
        execute_campaign(root, campaign_id, targets, runs)
        print("Campaign 执行结束。可运行 ./automan progress 查看状态。")
    return campaign_dir


def _load_matrix(raw: dict[str, Any]) -> TpccMatrix:
    warehouses = [int(value) for value in raw.get("warehouses", [])]
    terminals = [int(value) for value in raw.get("terminals", [])]
    if not warehouses:
        raise ValueError("matrix.warehouses must not be empty")
    if not terminals:
        raise ValueError("matrix.terminals must not be empty")
    return TpccMatrix(
        warehouses=warehouses,
        terminals=terminals,
        load_workers=int(raw.get("load_workers", 8)),
        run_mins=int(raw.get("run_mins", 60)),
    )


def _load_target(raw: dict[str, Any], profiles: dict, execution: dict[str, Any]) -> Target:
    profile_id = raw["profile"]
    profile = profiles[profile_id]
    connection = _load_connection(raw.get("connection", {}), execution)
    params = {str(key): str(value) for key, value in raw.get("database_parameters", {}).items()}
    manual_commands = list(raw.get("manual_parameter_commands") or build_manual_parameter_commands(profile, connection, params))
    return Target(
        profile=profile,
        connection=connection,
        recommended_params=params,
        accepted_params=params,
        apply_params=False,
        host_facts=dict(raw.get("host_facts", {})),
        mars3_options=dict(raw.get("mars3_options", {})),
        manual_parameter_commands=manual_commands,
        target_id=str(raw.get("id") or profile.id),
    )


def _load_connection(raw: dict[str, Any], execution: dict[str, Any]) -> ConnectionInfo:
    config = raw.get("config_ssh", {})
    database = raw.get("database", {})
    return ConnectionInfo(
        ssh_host=str(config.get("host", database.get("host", ""))),
        ssh_port=int(config.get("port", 22)),
        ssh_user=str(config.get("user", "")),
        ssh_password=str(config.get("password", "")),
        remote_workdir=str(config.get("workdir", "/root/automan")),
        db_host=str(database["host"]),
        db_port=int(database.get("port", 5432)),
        db_name=str(database.get("name", "postgres")),
        db_user=str(database["user"]),
        db_password=str(database.get("password", "")),
        postgresql_conf=raw.get("postgresql_conf"),
        restart_command=raw.get("restart_command"),
        gpconfig_command=str(raw.get("gpconfig_command", "gpconfig")),
        execution_host=str(execution.get("host", "172.16.100.143")),
        execution_port=int(execution.get("port", 22)),
        execution_user=str(execution.get("user", "root")),
        execution_password=str(execution.get("password", "")),
        execution_workdir=str(execution.get("workdir", "/root/automan")),
    )


def _merged_mars3_options(targets: list[Target]) -> dict[str, Any]:
    for target in targets:
        if target.mars3_options:
            return target.mars3_options
    return {}


def _print_task_plan(campaign_dir: Path, targets: list[Target], matrix: TpccMatrix, runs: list) -> None:
    print(f"Campaign 已生成: {campaign_dir}")
    print("Targets:")
    for target in targets:
        print(f"  - {target.id}: {target.profile.display_name}")
        print(f"    database: {target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}")
        if target.manual_parameter_commands:
            print("    manual parameter commands: generated")
    print("TPC-C Matrix:")
    print(f"  warehouses: {', '.join(map(str, matrix.warehouses))}")
    print(f"  terminals: {', '.join(map(str, matrix.terminals))}")
    print(f"  loadWorkers: {matrix.load_workers}")
    print(f"  runMins: {matrix.run_mins}")
    print(f"Total Runs: {len(runs)}")
    print(f"Manual parameter commands: {campaign_dir / 'manual-parameter-commands.sh'}")
