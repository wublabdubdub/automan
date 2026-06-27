from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.executor import execute_campaign
from automan_core.models import ConnectionInfo, Target, TpccMatrix
from automan_core.parameter_commands import build_manual_parameter_commands
from automan_core.plan import build_run_specs, new_campaign_id, write_campaign_files
from automan_core.profiles import find_profile, load_database_profiles


@dataclass(frozen=True)
class TaskDefinition:
    benchmark: str
    matrix: TpccMatrix
    targets: list[Target]
    campaign_id: str | None = None
    source_path: Path | None = None
    style: str = "legacy"


@dataclass(frozen=True)
class ValidationMessage:
    level: str
    text: str


def load_task_definition(root: Path, task_path: Path) -> TaskDefinition:
    raw = load_yaml(task_path)
    profiles = load_database_profiles(root)

    if raw.get("benchmark"):
        definition = _load_legacy_task(root, task_path, raw, profiles)
    elif "all" in raw:
        definition = _load_inventory_task(root, task_path, raw, profiles)
    else:
        raise ValueError("config must be a legacy task YAML or an inventory with all.children")

    failures = [message.text for message in validate_task_definition(definition) if message.level == "FAIL"]
    if failures:
        raise ValueError("; ".join(failures))
    return definition


def validate_task_definition(task: TaskDefinition) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []

    if task.benchmark != "tpcc":
        messages.append(ValidationMessage("FAIL", "only benchmark: tpcc is supported"))
    else:
        messages.append(ValidationMessage("OK", "benchmark: tpcc"))

    if task.matrix.warehouses and all(value > 0 for value in task.matrix.warehouses):
        messages.append(ValidationMessage("OK", f"warehouses: {', '.join(map(str, task.matrix.warehouses))}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpcc_warehouses/matrix.warehouses must contain positive integers"))

    if task.matrix.terminals and all(value > 0 for value in task.matrix.terminals):
        messages.append(ValidationMessage("OK", f"terminals: {', '.join(map(str, task.matrix.terminals))}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpcc_terminals/matrix.terminals must contain positive integers"))

    if task.matrix.load_workers > 0 and task.matrix.run_mins > 0:
        messages.append(ValidationMessage("OK", f"load_workers={task.matrix.load_workers}, run_mins={task.matrix.run_mins}"))
    else:
        messages.append(ValidationMessage("FAIL", "tpcc_load_workers and tpcc_run_mins must be positive"))

    if not task.targets:
        messages.append(ValidationMessage("FAIL", "at least one benchmark target is required"))
        return messages

    label = "target" if len(task.targets) == 1 else "targets"
    messages.append(ValidationMessage("OK", f"{len(task.targets)} benchmark {label} found"))

    for target in task.targets:
        connection = target.connection
        missing = []
        if not connection.execution_host:
            missing.append("execution host")
        if not connection.db_host:
            missing.append("db_host")
        if not connection.db_user:
            missing.append("db_user")
        if not connection.db_name:
            missing.append("db_name")
        if missing:
            messages.append(ValidationMessage("FAIL", f"{target.id}: missing {', '.join(missing)}"))
        else:
            messages.append(
                ValidationMessage(
                    "OK",
                    f"{target.id}: {target.profile.database_type} {connection.db_host}:{connection.db_port}/{connection.db_name}",
                )
            )

        if target.apply_params:
            messages.append(ValidationMessage("FAIL", f"{target.id}: parameter execution must remain manual-only"))
        elif target.manual_parameter_commands:
            messages.append(ValidationMessage("HINT", f"{target.id}: parameter changes are manual-only; commands can be rendered"))
        else:
            messages.append(ValidationMessage("WARN", f"{target.id}: no database parameter recommendations configured"))

        if target.profile.storage_engine == "mars3" and not target.mars3_options:
            messages.append(ValidationMessage("WARN", f"{target.id}: mars3 target has no mars3_options override"))

    return messages


def run_task_campaign(root: Path, task_path: Path, plan_only: bool) -> Path:
    task = load_task_definition(root, task_path)

    campaign_id = str(task.campaign_id or new_campaign_id())
    runs = build_run_specs(root, campaign_id, task.targets, task.matrix)
    campaign_dir = write_campaign_files(
        root,
        campaign_id,
        task.targets,
        task.matrix,
        runs,
        _merged_mars3_options(task.targets),
    )
    _print_task_plan(campaign_dir, task.targets, task.matrix, runs)

    if not plan_only:
        execute_campaign(root, campaign_id, task.targets, runs)
        print("[ OK ] campaign finished")
        print("[HINT] run ./automan progress to inspect status")
    return campaign_dir


def _load_legacy_task(root: Path, task_path: Path, raw: dict[str, Any], profiles: dict) -> TaskDefinition:
    if raw.get("benchmark") != "tpcc":
        raise ValueError("only benchmark: tpcc is supported in this phase")
    execution = raw.get("execution", {})
    matrix = _load_matrix(raw.get("matrix", {}))
    targets = [_load_legacy_target(item, profiles, execution) for item in raw.get("targets", [])]
    return TaskDefinition(
        benchmark="tpcc",
        matrix=matrix,
        targets=targets,
        campaign_id=raw.get("campaign_id"),
        source_path=task_path,
        style="legacy",
    )


def _load_inventory_task(root: Path, task_path: Path, raw: dict[str, Any], profiles: dict) -> TaskDefinition:
    all_group = _mapping(raw.get("all"), "all")
    all_vars = dict(all_group.get("vars", {}) or {})
    children = _mapping(all_group.get("children"), "all.children")
    bench = _mapping(children.get("bench"), "all.children.bench")
    execution = _load_inventory_execution(bench)

    targets: list[Target] = []
    matrix: TpccMatrix | None = None
    for group_name, child in children.items():
        if group_name == "bench":
            continue
        group = _mapping(child, f"all.children.{group_name}")
        group_vars = {**all_vars, **dict(group.get("vars", {}) or {})}
        if "db_type" not in group_vars:
            continue
        if not _has_tpcc_vars(group_vars):
            continue
        candidate = _load_inventory_matrix(group_vars)
        if matrix is None:
            matrix = candidate
        elif matrix != candidate:
            raise ValueError("all inventory benchmark targets must use the same tpcc_* matrix in this phase")
        targets.append(_load_inventory_target(group_name, group, group_vars, profiles, execution))

    if matrix is None:
        matrix = _load_inventory_matrix(all_vars)
    return TaskDefinition(
        benchmark=str(all_vars.get("benchmark", "tpcc")),
        matrix=matrix,
        targets=targets,
        campaign_id=all_vars.get("campaign_id"),
        source_path=task_path,
        style="inventory",
    )


def _load_matrix(raw: dict[str, Any]) -> TpccMatrix:
    warehouses = _int_list(raw.get("warehouses", []), "matrix.warehouses")
    terminals = _int_list(raw.get("terminals", []), "matrix.terminals")
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


def _load_inventory_matrix(vars_: dict[str, Any]) -> TpccMatrix:
    warehouses = _int_list(vars_.get("tpcc_warehouses", []), "tpcc_warehouses")
    terminals = _int_list(vars_.get("tpcc_terminals", []), "tpcc_terminals")
    if not warehouses:
        raise ValueError("tpcc_warehouses must not be empty")
    if not terminals:
        raise ValueError("tpcc_terminals must not be empty")
    return TpccMatrix(
        warehouses=warehouses,
        terminals=terminals,
        load_workers=int(vars_.get("tpcc_load_workers", 8)),
        run_mins=int(vars_.get("tpcc_run_mins", 60)),
    )


def _load_legacy_target(raw: dict[str, Any], profiles: dict, execution: dict[str, Any]) -> Target:
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


def _load_inventory_target(
    group_name: str,
    group: dict[str, Any],
    vars_: dict[str, Any],
    profiles: dict,
    execution: dict[str, Any],
) -> Target:
    host_name, host_vars = _first_host(group)
    merged = {**host_vars, **vars_}
    profile = _inventory_profile(merged, profiles)
    connection = _load_inventory_connection(host_name, merged, execution)
    params = {str(key): str(value) for key, value in dict(merged.get("database_parameters", {}) or {}).items()}
    manual_commands = list(merged.get("manual_parameter_commands") or build_manual_parameter_commands(profile, connection, params))
    mars3_options = dict(profile.mars3_defaults if profile.storage_engine == "mars3" else {})
    mars3_options.update(dict(merged.get("mars3_options", {}) or {}))
    return Target(
        profile=profile,
        connection=connection,
        recommended_params=params,
        accepted_params=params,
        apply_params=False,
        host_facts=_host_facts(merged),
        mars3_options=mars3_options,
        manual_parameter_commands=manual_commands,
        target_id=str(merged.get("target_id") or group_name),
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


def _load_inventory_execution(bench: dict[str, Any]) -> dict[str, Any]:
    _, host_vars = _first_host(bench)
    vars_ = {**dict(bench.get("vars", {}) or {}), **host_vars}
    return {
        "host": vars_.get("execution_host") or vars_.get("ansible_host") or vars_.get("host") or "172.16.100.143",
        "port": vars_.get("execution_port") or vars_.get("ansible_port") or vars_.get("port") or 22,
        "user": vars_.get("execution_user") or vars_.get("ansible_user") or vars_.get("user") or "root",
        "password": vars_.get("execution_password") or vars_.get("ansible_password") or vars_.get("password") or "",
        "workdir": vars_.get("execution_workdir") or vars_.get("automan_workdir") or vars_.get("workdir") or "/root/automan",
    }


def _load_inventory_connection(host_name: str, vars_: dict[str, Any], execution: dict[str, Any]) -> ConnectionInfo:
    db_host = str(vars_.get("db_host") or vars_.get("ansible_host") or host_name)
    return ConnectionInfo(
        ssh_host=str(vars_.get("config_host") or vars_.get("ansible_host") or db_host),
        ssh_port=int(vars_.get("config_port") or vars_.get("ansible_port") or 22),
        ssh_user=str(vars_.get("config_user") or vars_.get("ansible_user") or vars_.get("db_user") or ""),
        ssh_password=str(vars_.get("config_password") or vars_.get("ansible_password") or ""),
        remote_workdir=str(vars_.get("config_workdir") or vars_.get("automan_workdir") or "/root/automan"),
        db_host=db_host,
        db_port=int(vars_.get("db_port", 5432)),
        db_name=str(vars_.get("db_name", "postgres")),
        db_user=str(vars_.get("db_user", "")),
        db_password=str(vars_.get("db_password", "")),
        postgresql_conf=vars_.get("postgresql_conf"),
        restart_command=vars_.get("restart_command"),
        gpconfig_command=str(vars_.get("gpconfig_command", "gpconfig")),
        execution_host=str(execution.get("host", "172.16.100.143")),
        execution_port=int(execution.get("port", 22)),
        execution_user=str(execution.get("user", "root")),
        execution_password=str(execution.get("password", "")),
        execution_workdir=str(execution.get("workdir", "/root/automan")),
    )


def _inventory_profile(vars_: dict[str, Any], profiles: dict) -> Any:
    if vars_.get("profile"):
        return profiles[str(vars_["profile"])]
    return find_profile(
        profiles,
        database_type=str(vars_["db_type"]),
        storage_engine=str(vars_.get("storage_engine", "heap")),
        test_mode=str(vars_.get("test_mode", "single_node")),
    )


def _merged_mars3_options(targets: list[Target]) -> dict[str, Any]:
    for target in targets:
        if target.mars3_options:
            return target.mars3_options
    return {}


def _print_task_plan(campaign_dir: Path, targets: list[Target], matrix: TpccMatrix, runs: list) -> None:
    print(f"[ OK ] campaign planned: {campaign_dir}")
    print(f"[ OK ] targets: {len(targets)}")
    for target in targets:
        print(f"[HINT] target {target.id}: {target.profile.display_name}")
        print(f"[HINT] database {target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}")
        if target.manual_parameter_commands:
            print("[HINT] manual parameter commands: generated")
    print(
        "[ OK ] TPC-C matrix: "
        f"warehouses={','.join(map(str, matrix.warehouses))} "
        f"terminals={','.join(map(str, matrix.terminals))} "
        f"loadWorkers={matrix.load_workers} runMins={matrix.run_mins}"
    )
    print(f"[ OK ] total runs: {len(runs)}")
    print(f"[HINT] manual parameter commands: {campaign_dir / 'manual-parameter-commands.sh'}")


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


def _first_host(group: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    hosts = group.get("hosts", {}) or {}
    if isinstance(hosts, list):
        if not hosts:
            return "", {}
        return str(hosts[0]), {}
    if isinstance(hosts, dict) and hosts:
        name = next(iter(hosts))
        value = hosts[name] or {}
        if not isinstance(value, dict):
            value = {}
        return str(name), dict(value)
    return "", {}


def _has_tpcc_vars(vars_: dict[str, Any]) -> bool:
    return "tpcc_warehouses" in vars_ and "tpcc_terminals" in vars_


def _host_facts(vars_: dict[str, Any]) -> dict[str, str | int]:
    facts = dict(vars_.get("host_facts", {}) or {})
    for key in ("cpu_threads", "memory_gb"):
        if key in vars_ and key not in facts:
            facts[key] = vars_[key]
    return facts
