from __future__ import annotations

import argparse
from pathlib import Path

from automan_core.interactive import run_interactive_campaign
from automan_core.progress import show_progress


def main() -> None:
    parser = argparse.ArgumentParser(prog="automan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="start an interactive TPC-C campaign")
    run_parser.add_argument("--plan-only", action="store_true", help="write the campaign plan without executing")

    progress_parser = subparsers.add_parser("progress", help="show campaign progress")
    progress_parser.add_argument("--campaign", help="campaign id to inspect")
    progress_parser.add_argument("--watch", action="store_true", help="refresh until the campaign finishes")
    progress_parser.add_argument("--interval", type=int, default=5, help="watch refresh interval seconds")

    args = parser.parse_args()
    root = Path.cwd()

    if args.command == "run":
        run_interactive_campaign(root=root, plan_only=args.plan_only)
    elif args.command == "progress":
        show_progress(root=root, campaign_id=args.campaign, watch=args.watch, interval=args.interval)

