from __future__ import annotations

import argparse
from pathlib import Path

from automan_core.interactive import run_interactive_campaign
from automan_core.progress import show_progress
from automan_core.tooling import build_benchmarksql_on_linux, prompt_remote_execution_host


def main() -> None:
    parser = argparse.ArgumentParser(prog="automan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="start an interactive TPC-C campaign")
    run_parser.add_argument("--plan-only", action="store_true", help="write the campaign plan without executing")

    progress_parser = subparsers.add_parser("progress", help="show campaign progress")
    progress_parser.add_argument("--campaign", help="campaign id to inspect")
    progress_parser.add_argument("--watch", action="store_true", help="refresh until the campaign finishes")
    progress_parser.add_argument("--interval", type=int, default=5, help="watch refresh interval seconds")

    tools_parser = subparsers.add_parser("tools", help="manage external benchmark tools")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", required=True)
    build_parser = tools_subparsers.add_parser("build-benchmarksql", help="build BenchmarkSQL on Linux and sync dist/ back")
    build_parser.add_argument("--host", default="172.16.100.143", help="Linux execution host")
    build_parser.add_argument("--port", type=int, default=22, help="Linux SSH port")
    build_parser.add_argument("--user", default="root", help="Linux SSH user")
    build_parser.add_argument("--password", help="Linux SSH password; omit to prompt")
    build_parser.add_argument("--remote-workdir", default="/root/automan", help="remote automan path")

    args = parser.parse_args()
    root = Path.cwd()

    if args.command == "run":
        run_interactive_campaign(root=root, plan_only=args.plan_only)
    elif args.command == "progress":
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
