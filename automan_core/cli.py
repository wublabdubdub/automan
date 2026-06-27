from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from automan_core.checks import check_task_readiness
from automan_core.cleanup import cleanup_tpcc
from automan_core.progress import show_progress
from automan_core.report import generate_report, latest_campaign_id
from automan_core.task_runner import CampaignFailedError, load_task_definition, run_task_campaign, validate_task_definition
from automan_core.tooling import build_benchmarksql_on_linux, prompt_remote_execution_host


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate an automan inventory without connecting to DB")
    validate_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    check_parser = subparsers.add_parser("check", help="validate inventory and check database connectivity")
    check_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    param_parser = subparsers.add_parser("param", help="render manual DB parameter commands only")
    param_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    plan_parser = subparsers.add_parser("plan", help="render a campaign plan without executing benchmark runs")
    plan_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    run_parser = subparsers.add_parser("run", help="start a TPC-C campaign from inventory or legacy task YAML")
    run_source = run_parser.add_mutually_exclusive_group(required=True)
    run_source.add_argument("-i", "--inventory", help="inventory/config YAML path")
    run_source.add_argument("--task", help="legacy task YAML path")
    run_parser.add_argument("--plan-only", action="store_true", help="write the campaign plan without executing")

    report_parser = subparsers.add_parser("report", help="generate Markdown report for a campaign")
    report_parser.add_argument("-i", "--inventory", help="inventory/config YAML path; accepted for playbook compatibility")
    report_parser.add_argument("--campaign", help="campaign id; defaults to latest campaign")

    cleanup_parser = subparsers.add_parser("cleanup", help="drop bmsql_%% TPC-C objects for inventory targets")
    cleanup_parser.add_argument("-i", "--inventory", required=True, help="inventory/config YAML path")

    progress_parser = subparsers.add_parser("progress", help="show campaign progress")
    progress_parser.add_argument("--campaign", help="campaign id to inspect")
    progress_parser.add_argument("--watch", action="store_true", help="refresh until the campaign finishes")
    progress_parser.add_argument("--interval", type=int, default=5, help="watch refresh interval seconds")

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
    elif args.command == "param":
        campaign_dir = run_task_campaign(root=root, task_path=Path(args.inventory), plan_only=True)
        print_status("OK", f"manual parameter commands: {campaign_dir / 'manual-parameter-commands.sh'}")
        print_status("HINT", "review and execute the generated script manually; automan never applies DB parameters")
    elif args.command == "plan":
        campaign_dir = run_task_campaign(root=root, task_path=Path(args.inventory), plan_only=True)
        print_status("OK", f"campaign plan: {campaign_dir}")
    elif args.command == "run":
        source = Path(args.inventory or args.task)
        try:
            run_task_campaign(root=root, task_path=source, plan_only=args.plan_only)
        except CampaignFailedError:
            raise SystemExit(1)
    elif args.command == "report":
        campaign_id = args.campaign or latest_campaign_id(root)
        if not campaign_id:
            print_status("FAIL", "no campaign found under runs/campaigns")
            raise SystemExit(1)
        try:
            report_path = generate_report(root, campaign_id)
        except FileNotFoundError as exc:
            print_status("FAIL", f"campaign {campaign_id} is missing required file: {exc.filename}")
            raise SystemExit(1) from exc
        print_status("OK", f"report: {report_path}")
    elif args.command == "cleanup":
        failures = cleanup_tpcc(root, Path(args.inventory))
        if failures:
            raise SystemExit(1)
    elif args.command == "progress":
        print_status("OK", "showing campaign progress")
        show_progress(root=root, campaign_id=args.campaign, watch=args.watch, interval=args.interval)
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
    parser.add_argument("-c", "--config", required=True, help="template name under conf/, such as tpcc/pg")
    parser.add_argument("-o", "--output", default="automan.yml", help="output path")
    args = parser.parse_args()

    root = Path.cwd()
    template = _template_path(root, args.config)
    output = root / args.output
    if not template.exists():
        print_status("FAIL", f"template not found: {template}")
        raise SystemExit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, output)
    print_status("OK", f"configured {args.config} -> {output}")
    print_status("HINT", f"next: ./bin/validate -i {args.output}")


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
