from __future__ import annotations

from getpass import getpass
from pathlib import Path
from typing import Any

from automan_core.executor import execute_campaign
from automan_core.models import ConnectionInfo, Target, TpccMatrix
from automan_core.plan import build_run_specs, new_campaign_id, write_campaign_files
from automan_core.profiles import find_profile, load_database_profiles, load_database_types
from automan_core.sizing import probe_host, recommend_params, recommended_load_workers
from automan_core.ssh import SSHClient


def run_interactive_campaign(root: Path, plan_only: bool) -> None:
    print("automan TPC-C campaign")
    print()
    database_types = load_database_types(root)
    profiles = load_database_profiles(root)

    selected_types = _choose_many(
        "请选择数据库类型，可多选",
        [(key, value["display_name"]) for key, value in database_types.items()],
    )
    selected_profiles = []
    for db_type in selected_types:
        type_config = database_types[db_type]
        storage_engines = [item["name"] for item in type_config["storage_engines"]]
        test_modes = [item["name"] for item in type_config["test_modes"]]

        if len(storage_engines) == 1:
            chosen_engines = storage_engines
            print(f"{type_config['display_name']} 存储引擎默认: {storage_engines[0]}")
        else:
            choices = [(item["name"], item["display_name"]) for item in type_config["storage_engines"]]
            chosen_engines = _choose_many(f"请选择 {type_config['display_name']} 存储引擎，可多选", choices)

        if len(test_modes) == 1:
            chosen_modes = test_modes
            print(f"{type_config['display_name']} 测试模式默认: {test_modes[0]}")
        else:
            choices = [(item["name"], item["display_name"]) for item in type_config["test_modes"]]
            chosen_modes = _choose_many(f"请选择 {type_config['display_name']} 测试模式，可多选", choices)

        for engine in chosen_engines:
            for mode in chosen_modes:
                selected_profiles.append(find_profile(profiles, db_type, engine, mode))

    print()
    print("将配置以下 target:")
    for profile in selected_profiles:
        print(f"  - {profile.id}: {profile.display_name}")

    execution = _prompt_execution_context()
    matrix_seed = _prompt_matrix_seed()
    target_connections: list[Target] = []
    reusable_connections: list[ConnectionInfo] = []
    max_terminals = max(matrix_seed.terminals)

    for profile in selected_profiles:
        print()
        print(f"配置 target: {profile.id}")
        connection = _prompt_connection(profile.database_type, reusable_connections, execution)
        reusable_connections.append(connection)
        facts = probe_host(SSHClient(connection.ssh_host, connection.ssh_port, connection.ssh_user, connection.ssh_password))
        _print_facts(facts)
        recommended = recommend_params(profile, facts, max_terminals=max_terminals)
        accepted, apply_params = _confirm_params(recommended)
        target_connections.append(
            Target(
                profile=profile,
                connection=connection,
                recommended_params=recommended,
                accepted_params=accepted,
                apply_params=apply_params,
                host_facts=facts,
            )
        )

    load_workers_default = max(recommended_load_workers(target.host_facts) for target in target_connections) if target_connections else 8
    matrix = _prompt_matrix(load_workers_default, matrix_seed)
    mars3_options = _prompt_mars3_options(selected_profiles)
    for target in target_connections:
        if target.profile.storage_engine == "mars3":
            target.mars3_options = mars3_options
    _confirm_ddl(selected_profiles, mars3_options)

    campaign_id = new_campaign_id()
    runs = build_run_specs(root, campaign_id, target_connections, matrix)
    _print_plan(target_connections, matrix, runs, mars3_options)
    if plan_only:
        campaign_dir = write_campaign_files(root, campaign_id, target_connections, matrix, runs, mars3_options)
        print(f"plan-only 模式，已写入计划但不执行: {campaign_dir}")
        return
    if not _yes_no("是否开始执行? [y/N]", default=False):
        campaign_dir = write_campaign_files(root, campaign_id, target_connections, matrix, runs, mars3_options)
        print(f"已写入计划但未执行: {campaign_dir}")
        return

    campaign_dir = write_campaign_files(root, campaign_id, target_connections, matrix, runs, mars3_options)
    print(f"Campaign 已生成: {campaign_dir}")
    execute_campaign(root, campaign_id, target_connections, runs)
    print("Campaign 执行结束。可运行 ./automan progress 查看状态。")


def _prompt_matrix_seed() -> TpccMatrix:
    print()
    warehouses = _prompt_int_list("请输入 warehouses，可多选，例如 100,1000")
    terminals = _prompt_int_list("请输入 terminals，可多选，例如 100,500,1000")
    return TpccMatrix(warehouses=warehouses, terminals=terminals, load_workers=0, run_mins=60)


def _prompt_matrix(load_workers_default: int, seed: TpccMatrix) -> TpccMatrix:
    print()
    run_mins = _prompt_int("请输入 runMins", default=60)
    load_workers = _prompt_int("请输入 loadWorkers", default=load_workers_default)
    return TpccMatrix(seed.warehouses, seed.terminals, load_workers, run_mins)


def _prompt_execution_context() -> dict[str, str | int]:
    print()
    print("执行机配置（automan 和 BenchmarkSQL 实际运行的位置）")
    return {
        "execution_host": _prompt("执行机 host", default="172.16.100.143"),
        "execution_port": _prompt_int("执行机 SSH port", default=22),
        "execution_user": _prompt("执行机 SSH user", default="root"),
        "execution_password": _prompt_secret_optional("执行机 SSH password（仅用于工具构建/同步，可留空）"),
        "execution_workdir": _prompt("执行机 automan 路径", default="/root/automan"),
    }


def _prompt_connection(database_type: str, reusable: list[ConnectionInfo], execution: dict[str, str | int]) -> ConnectionInfo:
    if reusable:
        print("可复用已有服务器配置:")
        for idx, conn in enumerate(reusable, start=1):
            print(f"  [{idx}] config={conn.ssh_host}/{conn.ssh_user}, db={conn.db_host}:{conn.db_port}")
        print(f"  [{len(reusable) + 1}] 新增服务器")
        choice = _prompt_int("请选择", default=len(reusable) + 1)
        if 1 <= choice <= len(reusable):
            base = reusable[choice - 1]
            return _prompt_database_connection(database_type, base)

    print("数据库配置 SSH（用于探测 CPU/内存、改 postgresql.conf 或执行 gpconfig/mxstop）")
    ssh_host = _prompt("配置 SSH host")
    ssh_port = _prompt_int("配置 SSH port", default=22)
    ssh_user = _prompt("配置 SSH user")
    ssh_password = _prompt_secret("配置 SSH password")
    remote_workdir = _prompt("配置 SSH 工作目录", default="/root/automan")
    base = ConnectionInfo(
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        remote_workdir=remote_workdir,
        db_host=ssh_host,
        db_port=5432,
        db_name="postgres",
        db_user="",
        db_password="",
        execution_host=str(execution["execution_host"]),
        execution_port=int(execution["execution_port"]),
        execution_user=str(execution["execution_user"]),
        execution_password=str(execution["execution_password"]),
        execution_workdir=str(execution["execution_workdir"]),
    )
    return _prompt_database_connection(database_type, base)


def _prompt_database_connection(database_type: str, base: ConnectionInfo) -> ConnectionInfo:
    db_host = _prompt("数据库 host", default=base.ssh_host)
    db_port = _prompt_int("数据库 port", default=5432)
    db_name = _prompt("数据库名", default="postgres")
    db_user = _prompt("数据库用户")
    db_password = _prompt_secret("数据库密码")
    postgresql_conf = None
    restart_command = None
    gpconfig_command = "gpconfig"
    if database_type == "postgresql":
        postgresql_conf = _prompt("postgresql.conf 路径")
        restart_command = _prompt("PostgreSQL 重启命令")
    else:
        gpconfig_command = _prompt("gpconfig 命令路径", default="gpconfig")
        restart_command = _prompt("YMatrix 重启命令", default="mxstop -afr")
    return ConnectionInfo(
        ssh_host=base.ssh_host,
        ssh_port=base.ssh_port,
        ssh_user=base.ssh_user,
        ssh_password=base.ssh_password,
        remote_workdir=base.remote_workdir,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        postgresql_conf=postgresql_conf,
        restart_command=restart_command,
        gpconfig_command=gpconfig_command,
        execution_host=base.execution_host,
        execution_port=base.execution_port,
        execution_user=base.execution_user,
        execution_password=base.execution_password,
        execution_workdir=base.execution_workdir,
    )


def _confirm_params(params: dict[str, str]) -> tuple[dict[str, str], bool]:
    print()
    print("推荐数据库参数:")
    for key, value in params.items():
        print(f"  {key} = {value}")
    print("请选择:")
    print("  [1] 接受并应用")
    print("  [2] 修改后应用")
    print("  [3] 不应用参数，继续测试")
    choice = _prompt_int("请输入选项", default=1)
    if choice == 3:
        return params, False
    if choice == 2:
        edited = {}
        for key, value in params.items():
            edited[key] = _prompt(key, default=value)
        return edited, True
    return params, True


def _prompt_mars3_options(profiles: list) -> dict[str, Any]:
    mars3_profiles = [profile for profile in profiles if profile.storage_engine == "mars3"]
    if not mars3_profiles:
        return {}
    defaults = mars3_profiles[0].mars3_defaults
    print()
    print("请确认 MARS3 建表参数，直接回车使用默认值:")
    return {
        "prefer_load_mode": _prompt("prefer_load_mode", default=str(defaults.get("prefer_load_mode", "single"))),
        "rowstore_size": _prompt_int("rowstore_size", default=int(defaults.get("rowstore_size", 64))),
        "compresstype": _prompt("compresstype", default=str(defaults.get("compresstype", "zstd"))),
        "compresslevel": _prompt_int("compresslevel", default=int(defaults.get("compresslevel", 1))),
        "compress_threshold": _prompt_int("compress_threshold", default=int(defaults.get("compress_threshold", 1200))),
    }


def _confirm_ddl(profiles: list, mars3_options: dict[str, Any]) -> None:
    print()
    print("DDL profile 确认:")
    for profile in profiles:
        print(f"  - {profile.id}: {profile.ddl_profile} ({profile.ddl_dir})")
        if profile.storage_engine == "mars3":
            print(f"    MARS3 参数: {mars3_options}")
    if not _yes_no("是否确认使用以上 DDL profile? [y/N]", default=False):
        raise SystemExit("DDL profile 未确认，终止。")


def _print_plan(targets: list[Target], matrix: TpccMatrix, runs: list, mars3_options: dict[str, Any]) -> None:
    print()
    print("本次 Campaign 计划:")
    print("Targets:")
    for target in targets:
        print(f"  - {target.id}")
        print(f"    execution: {target.connection.execution_host}:{target.connection.execution_workdir}")
        print(f"    config ssh: {target.connection.ssh_host}")
        print(f"    database: {target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}")
        print(f"    ddl: {target.profile.ddl_profile}")
    print("TPC-C Matrix:")
    print(f"  warehouses: {', '.join(map(str, matrix.warehouses))}")
    print(f"  terminals: {', '.join(map(str, matrix.terminals))}")
    print(f"  loadWorkers: {matrix.load_workers}")
    print(f"  runMins: {matrix.run_mins}")
    if mars3_options:
        print(f"MARS3: {mars3_options}")
    print(f"Total Runs: {len(runs)}")
    print("执行顺序:")
    print("  target 的第一条 run: runDatabaseBuild.sh -> runBenchmark.sh")
    print("  target 的后续 run:")
    print("  runDatabaseDestroy.sh")
    print("  runDatabaseBuild.sh")
    print("  runBenchmark.sh")
    print("调度: 同数据库 host 串行，不同数据库 host 并行")


def _print_facts(facts: dict[str, str | int]) -> None:
    print()
    print("服务器探测结果:")
    print(f"  CPU threads: {facts.get('cpu_threads', 'unknown')}")
    print(f"  Memory GB: {facts.get('memory_gb', 'unknown')}")
    if facts.get("probe_exit_code") != 0:
        print(f"  Probe warning: {facts.get('probe_stderr')}")


def _choose_many(prompt: str, choices: list[tuple[str, str]]) -> list[str]:
    print()
    print(prompt + ":")
    for idx, (_, label) in enumerate(choices, start=1):
        print(f"  [{idx}] {label}")
    while True:
        raw = input("> ").strip()
        try:
            indexes = [int(item.strip()) for item in raw.split(",") if item.strip()]
            selected = [choices[i - 1][0] for i in indexes]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("请输入有效编号，例如 1 或 1,2")


def _prompt_int_list(prompt: str) -> list[int]:
    while True:
        raw = input(prompt + "\n> ").strip()
        try:
            values = [int(item.strip()) for item in raw.split(",") if item.strip()]
            if values:
                return values
        except ValueError:
            pass
        print("请输入逗号分隔的整数，例如 100,1000")


def _prompt(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    return _prompt(prompt, default)


def _prompt_secret(prompt: str) -> str:
    value = getpass(f"{prompt}: ").strip()
    if value:
        return value
    return _prompt_secret(prompt)


def _prompt_secret_optional(prompt: str) -> str:
    return getpass(f"{prompt}: ").strip()


def _prompt_int(prompt: str, default: int | None = None) -> int:
    while True:
        raw = _prompt(prompt, str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            print("请输入整数。")


def _yes_no(prompt: str, default: bool) -> bool:
    value = input(prompt + " ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}
