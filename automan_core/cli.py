from __future__ import annotations

import argparse
import copy
import shutil
from pathlib import Path
from typing import Callable, Dict, Iterable, Union

from automan_core.checks import check_task_readiness
from automan_core.clean_stale_runs import clean_stale_runs
from automan_core.config import load_yaml, write_yaml
from automan_core.delete_results import delete_results
from automan_core.list_results import show_completed_results
from automan_core.models import Target
from automan_core.progress import show_progress
from automan_core.report import generate_report, latest_job_id
from automan_core.sizing import probe_host
from automan_core.ssh import SSHClient
from automan_core.task_runner import (
    AUTO_PARAMETER_DATABASE_TYPES,
    JobFailedError,
    live_parameter_conflicts,
    load_task_definition,
    refresh_auto_parameter_commands,
    run_task_job,
    validate_task_definition,
)
from automan_core.ts import kafka_check
from automan_core.tooling import build_benchmarksql_on_linux, prompt_remote_execution_host


CONFIG_ALIASES: dict[str, tuple[str, ...]] = {
    "pg": ("pg",),
    "postgres": ("pg",),
    "postgresql": ("pg",),
    "tpcc/pg": ("pg",),
    "tpcc/postgres": ("pg",),
    "tpcc/postgresql": ("pg",),
    "ym-heap": ("ym-heap",),
    "ymatrix-heap": ("ym-heap",),
    "ymatrix_heap": ("ym-heap",),
    "ym-heap-master-only": ("ym-heap",),
    "ymatrix-heap-master-only": ("ym-heap",),
    "tpcc/ym-heap": ("ym-heap",),
    "tpcc/ymatrix-heap": ("ym-heap",),
    "ym-mars3": ("ym-mars3",),
    "ymatrix-mars3": ("ym-mars3",),
    "ymatrix_mars3": ("ym-mars3",),
    "ym-mars3-master-only": ("ym-mars3",),
    "ymatrix-mars3-master-only": ("ym-mars3",),
    "tpcc/ym-mars3": ("ym-mars3",),
    "tpcc/ymatrix-mars3": ("ym-mars3",),
    "tpcc/pg-vs-ymatrix": ("pg", "ym-heap", "ym-mars3"),
    "pg-vs-ymatrix": ("pg", "ym-heap", "ym-mars3"),
}

CONFIG_TARGETS = ("pg", "ym-heap", "ym-mars3")
CONFIG_TYPES = ("tpcc", "ts", "ap", "tpch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate an automan inventory without connecting to DB")
    validate_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    check_parser = subparsers.add_parser("check", help="validate inventory and check database connectivity")
    check_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    kafka_parser = subparsers.add_parser("kafka-check", help="check or prepare TS Kafka topic")
    kafka_parser.add_argument("-i", "--inventory", required=True, help="TS inventory/config YAML path")
    kafka_parser.add_argument("--apply", action="store_true", help="delete/create/describe the configured topic")

    param_parser = subparsers.add_parser("param", help="render manual DB parameter commands only")
    param_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")
    param_parser.add_argument("--offline", action="store_true", help="use configured host_facts instead of probing target hosts")

    plan_parser = subparsers.add_parser("plan", help="render a job plan without executing benchmark runs")
    plan_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    run_parser = subparsers.add_parser("run", help="start a TPC-C job from inventory or legacy task YAML")
    run_source = run_parser.add_mutually_exclusive_group(required=True)
    run_source.add_argument("-i", "--inventory", help="inventory/config YAML path")
    run_source.add_argument("--task", help="legacy task YAML path")
    run_parser.add_argument("--plan-only", action="store_true", help="write the job plan without executing")
    run_parser.add_argument(
        "--stage",
        choices=["load", "destroy", "bench", "ts-write", "ts-query", "point-query", "ap-query", "tpch-load", "tpch-query"],
        help="run only one benchmark stage",
    )

    report_parser = subparsers.add_parser("report", help="generate Markdown report for a job")
    report_parser.add_argument("-i", "--inventory", help="inventory/config YAML path; accepted for playbook compatibility")
    report_parser.add_argument("--job", help="job id; defaults to latest job")

    progress_parser = subparsers.add_parser("progress", help="show current TPC-C job progress")
    progress_parser.add_argument("--job", help="job id; defaults to the running job")
    progress_parser.add_argument("--watch", nargs="?", const=5, type=int, help="refresh every N seconds; default is 5")

    list_parser = subparsers.add_parser("list", help="list completed benchmark results")
    list_parser.add_argument("-t", "--type", choices=CONFIG_TYPES, default="tpcc", help="benchmark type to list")
    list_parser.add_argument("--job", help="only list completed results for this job")

    delete_parser = subparsers.add_parser("delete", help="delete one or more benchmark results by ID")
    delete_parser.add_argument("ids", nargs="+", help="result ID(s) shown by list, or full run ID(s)")
    delete_parser.add_argument("-f", "--force", action="store_true", help="delete without typing DELETE")

    clean_parser = subparsers.add_parser("clean", help="clean stale running runs whose process is gone or failed runs")
    clean_parser.add_argument("--job", help="only clean stale or failed runs in this job")
    clean_parser.add_argument("-f", "--force", action="store_true", help="clean without typing CLEAN")

    tools_parser = subparsers.add_parser("tools", help="manage external benchmark tools")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", required=True)
    build_parser_ = tools_subparsers.add_parser("build-benchmarksql", help="build BenchmarkSQL on Linux and sync dist/ back")
    build_parser_.add_argument("--host", default="172.16.100.143", help="Linux execution host")
    build_parser_.add_argument("--port", type=int, default=22, help="Linux SSH port")
    build_parser_.add_argument("--user", default="root", help="Linux SSH user")
    build_parser_.add_argument("--password", help="Linux SSH password; omit to prompt")
    build_parser_.add_argument("--remote-workdir", default="/root/automan", help="remote automan path")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    root = Path.cwd()

    if args.command == "validate":
        failures = validate_inventory(root, Path(args.inventory))
        if failures:
            raise SystemExit(1)
    elif args.command == "check":
        failures = check_inventory(root, Path(args.inventory))
        if failures:
            raise SystemExit(1)
    elif args.command == "kafka-check":
        failures = kafka_check(root, Path(args.inventory), apply=args.apply)
        if failures:
            raise SystemExit(failures)
    elif args.command == "param":
        failures = render_param_commands(root, Path(args.inventory), offline=args.offline)
        if failures:
            raise SystemExit(failures)
    elif args.command == "plan":
        job_dir = run_task_job(root=root, task_path=Path(args.inventory), plan_only=True)
        print_status("OK", f"job plan: {job_dir}")
    elif args.command == "run":
        source = Path(args.inventory or args.task)
        try:
            run_task_job(root=root, task_path=source, plan_only=args.plan_only, stage=args.stage)
        except ValueError as exc:
            print_status("FAIL", str(exc))
            raise SystemExit(1) from exc
        except JobFailedError:
            raise SystemExit(1)
    elif args.command == "report":
        job_id = args.job or latest_job_id(root)
        if not job_id:
            print_status("FAIL", "no job found under runs/jobs")
            raise SystemExit(1)
        try:
            report_path = generate_report(root, job_id)
        except FileNotFoundError as exc:
            print_status("FAIL", f"job {job_id} is missing required file: {exc.filename}")
            raise SystemExit(1) from exc
        print_status("OK", f"report: {report_path}")
    elif args.command == "list":
        failures = show_completed_results(root=root, job_id=args.job, benchmark_type=args.type)
        if failures:
            raise SystemExit(failures)
    elif args.command == "delete":
        try:
            failures = delete_results(root=root, result_ids=args.ids, force=args.force)
        except ValueError as exc:
            print_status("FAIL", str(exc))
            raise SystemExit(1) from exc
        if failures:
            raise SystemExit(failures)
    elif args.command == "clean":
        try:
            failures = clean_stale_runs(root=root, job_id=args.job, force=args.force)
        except ValueError as exc:
            print_status("FAIL", str(exc))
            raise SystemExit(1) from exc
        if failures:
            raise SystemExit(failures)
    elif args.command == "progress":
        failures = show_progress(root=root, job_id=args.job, watch_seconds=args.watch)
        if failures:
            raise SystemExit(failures)
    elif args.command == "tools" and args.tools_command == "build-benchmarksql":
        remote = prompt_remote_execution_host(args)
        results = build_benchmarksql_on_linux(root=root, remote=remote)
        for result in results:
            print(f"$ {result.command}")
            if result.stdout:
                print(result.stdout.rstrip())
            if result.stderr:
                print(result.stderr.rstrip())
            if result.exit_code != 0:
                raise SystemExit(result.exit_code)


def configure_main() -> None:
    parser = argparse.ArgumentParser(prog="configure")
    parser.add_argument("-t", "--type", choices=CONFIG_TYPES, default="tpcc", help="benchmark type")
    parser.add_argument("-c", "--config", required=True, help="target alias list such as pg, ym-heap, or pg,ym-heap")
    parser.add_argument("-o", "--output", default="automan.yml", help="output path")
    args = parser.parse_args()

    root = Path.cwd()
    output = root / args.output
    try:
        aliases = _configure_aliases(root, args.config)
    except ValueError as exc:
        print_status("FAIL", str(exc))
        raise SystemExit(1)
    output.parent.mkdir(parents=True, exist_ok=True)

    if aliases:
        inventory = _compose_inventory(root, args.type, aliases)
        write_yaml(output, inventory)
        target_names = ", ".join(_configured_target_names(inventory))
        print_status("OK", f"configured targets: {', '.join(aliases)} -> {output}")
        print_status("OK", f"inventory targets: {target_names}")
    else:
        template = _template_path(root, args.config)
        shutil.copyfile(template, output)
        print_status("OK", f"configured {args.type}/{args.config} -> {output}")
    print_status("HINT", f"next: ./check.yml -i {args.output}")


def validate_inventory(root: Path, inventory: Path) -> int:
    try:
        task = load_task_definition(root, inventory)
        messages = validate_task_definition(task)
    except Exception as exc:
        print_status("FAIL", str(exc))
        return 1

    failures = 0
    for message in messages:
        print_status(message.level, message.text)
        if message.level == "FAIL":
            failures += 1
    if failures:
        print_status("HINT", "fix the inventory and run validate again")
    else:
        print_status("HINT", "parameter changes are manual-only; render them with ./automan param -i <inventory>")
    return failures


def check_inventory(root: Path, inventory: Path) -> int:
    failures = validate_inventory(root, inventory)
    if failures:
        return failures
    task = load_task_definition(root, inventory)
    for result in check_task_readiness(root, task):
        print_status(result.level, result.text)
        if result.level == "FAIL":
            failures += 1
    return failures


FactProvider = Callable[[Target], Dict[str, Union[str, int]]]


def render_param_commands(root: Path, inventory: Path, offline: bool = False, fact_provider: FactProvider | None = None) -> int:
    try:
        task = load_task_definition(root, inventory)
    except Exception as exc:
        print_status("FAIL", str(exc))
        return 1

    if not offline:
        failures = _refresh_live_parameter_facts(task, fact_provider)
        if failures:
            return failures
    else:
        print_status("WARN", "offline parameter rendering; using configured host_facts without probing target hosts")

    print_status("OK", f"manual parameter commands for {len(task.targets)} target(s)")
    print_status("HINT", "printed only; no shell file is generated and automan does not execute these commands")
    for target in task.targets:
        print()
        print(f"# target: {target.id} ({target.profile.display_name})")
        print(f"# database: {target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}")
        if not target.manual_parameter_commands:
            print("# no parameter commands declared")
            continue
        for command in target.manual_parameter_commands:
            print(command)
    return 0


def _refresh_live_parameter_facts(task, fact_provider: FactProvider | None = None) -> int:
    failures = 0
    provider = fact_provider or _probe_target_facts
    for target in task.targets:
        if target.profile.database_type not in AUTO_PARAMETER_DATABASE_TYPES:
            continue
        if not target.manual_parameter_commands_auto_generated:
            continue
        try:
            facts = provider(target)
        except RuntimeError as exc:
            print_status("FAIL", f"{target.id}: {exc}")
            failures += 1
            continue
        max_terminals = max(task.matrix.terminals) if task.matrix is not None else 1
        conflicts = live_parameter_conflicts(target, facts, max_terminals)
        if conflicts:
            conflict_text = ", ".join(f"{key}={old} (live recommendation {new})" for key, (old, new) in conflicts.items())
            print_status(
                "FAIL",
                f"{target.id}: database_parameters conflict with live host facts: {conflict_text}",
            )
            print_status(
                "HINT",
                f"{target.id}: remove stale auto-managed database_parameters, or use --offline if these fixed values are intentional",
            )
            failures += 1
            continue
        refresh_auto_parameter_commands(target, facts, max_terminals)
        print_status(
            "OK",
            f"{target.id}: probed host facts cpu_threads={facts.get('cpu_threads')} memory_gb={facts.get('memory_gb')}",
        )
    if failures:
        print_status("HINT", "fix SSH/config_host/config_user/config_password, or rerun with --offline to explicitly use configured host_facts")
    return failures


def _probe_target_facts(target: Target) -> dict[str, str | int]:
    connection = target.connection
    host = connection.ssh_host or connection.db_host
    if not host:
        raise RuntimeError("cannot probe host facts because config_host/db_host is empty")
    if not connection.ssh_user:
        raise RuntimeError("cannot probe host facts because config_user is empty")
    client = SSHClient(host=host, port=connection.ssh_port, user=connection.ssh_user, password=connection.ssh_password)
    facts = probe_host(client)
    if facts.get("probe_exit_code") != 0:
        error = str(facts.get("probe_stderr") or facts.get("probe_stdout") or "host probe failed").strip()
        raise RuntimeError(f"host fact probe failed on {connection.ssh_user}@{host}:{connection.ssh_port}: {error}")
    missing = [key for key in ("cpu_threads", "memory_gb") if key not in facts]
    if missing:
        raise RuntimeError(f"host fact probe did not return {', '.join(missing)} from {connection.ssh_user}@{host}:{connection.ssh_port}")
    return facts


def print_status(level: str, message: str) -> None:
    tags = {
        "OK": "[ OK ]",
        "WARN": "[WARN]",
        "FAIL": "[FAIL]",
        "HINT": "[HINT]",
    }
    print(f"{tags.get(level, '[INFO]')} {message}")


def _template_path(root: Path, name: str) -> Path:
    candidate = name if name.endswith((".yml", ".yaml")) else f"{name}.yml"
    return root / "conf" / candidate


def _configure_aliases(root: Path, config: str) -> list[str]:
    parts = [part.strip() for part in config.split(",") if part.strip()]
    if not parts:
        raise ValueError("-c/--config must contain at least one target alias")

    aliases: list[str] = []
    unknown: list[str] = []
    for part in parts:
        normalized = _normalize_config_name(part)
        mapped = CONFIG_ALIASES.get(normalized)
        if mapped is None:
            unknown.append(part)
            continue
        for alias in mapped:
            if alias not in aliases:
                aliases.append(alias)

    if unknown:
        if len(parts) == 1:
            template = _template_path(root, config)
            if template.exists():
                return []
        available = ", ".join(CONFIG_TARGETS)
        raise ValueError(f"unknown config alias: {', '.join(unknown)}; available: {available}")
    return aliases


def _normalize_config_name(name: str) -> str:
    normalized = name.strip().replace("\\", "/").lower()
    if normalized.startswith("conf/"):
        normalized = normalized[len("conf/"):]
    if normalized.endswith((".yml", ".yaml")):
        normalized = normalized.rsplit(".", 1)[0]
    for prefix in ("tpcc/targets/", "ts/targets/", "ap/targets/", "tpch/targets/", "tpcc/", "ts/", "ap/", "tpch/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized


def _compose_tpcc_inventory(root: Path, aliases: Iterable[str]) -> dict:
    return _compose_inventory(root, "tpcc", aliases)


def _compose_inventory(root: Path, benchmark: str, aliases: Iterable[str]) -> dict:
    base_path = root / "conf" / benchmark / "base.yml"
    if not base_path.exists():
        raise ValueError(f"template base not found: {base_path}")
    inventory = copy.deepcopy(load_yaml(base_path))
    children = _inventory_children(inventory, base_path)

    for alias in aliases:
        target_path = root / "conf" / benchmark / "targets" / f"{alias}.yml"
        if not target_path.exists():
            raise ValueError(f"target fragment not found for {alias}: {target_path}")
        fragment = load_yaml(target_path)
        for name, child in _inventory_children(fragment, target_path).items():
            if name == "bench":
                raise ValueError(f"target fragment must not define bench group: {target_path}")
            if name in children:
                raise ValueError(f"duplicate inventory group {name!r} from {target_path}")
            children[name] = child
    return inventory


def _inventory_children(inventory: dict, path: Path) -> dict:
    all_group = inventory.get("all")
    if not isinstance(all_group, dict):
        raise ValueError(f"{path} must contain an all mapping")
    children = all_group.setdefault("children", {})
    if not isinstance(children, dict):
        raise ValueError(f"{path} all.children must be a mapping")
    return children


def _configured_target_names(inventory: dict) -> list[str]:
    children = _inventory_children(inventory, Path("<generated>"))
    return [name for name in children if name != "bench"]
