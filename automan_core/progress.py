from __future__ import annotations

import time
from pathlib import Path

from automan_core.config import load_yaml


def show_progress(root: Path, campaign_id: str | None, watch: bool, interval: int) -> None:
    while True:
        campaign_dir = _find_campaign_dir(root, campaign_id)
        if not campaign_dir:
            print("No campaign found.")
            return
        progress = load_yaml(campaign_dir / "progress.json")
        _print_progress(progress)
        if not watch or progress.get("status") in {"success", "failed", "cancelled"}:
            return
        time.sleep(interval)


def _find_campaign_dir(root: Path, campaign_id: str | None) -> Path | None:
    base = root / "runs" / "campaigns"
    if campaign_id:
        path = base / campaign_id
        return path if (path / "progress.json").exists() else None
    candidates = sorted(base.glob("*/progress.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].parent if candidates else None


def _print_progress(progress: dict) -> None:
    print(f"Campaign: {progress.get('campaign_id')}")
    print(f"Status: {progress.get('status')}")
    print(
        "Progress: "
        f"{progress.get('finished_runs', 0)}/{progress.get('total_runs', 0)} finished, "
        f"{progress.get('running_runs', 0)} running, "
        f"{progress.get('pending_runs', 0)} pending, "
        f"{progress.get('failed_runs', 0)} failed"
    )
    print()
    print(f"{'TARGET':32} {'DB HOST':18} {'CURRENT RUN':38} {'PHASE':24} {'DONE'}")
    for target in progress.get("targets", []):
        done = f"{target.get('finished_runs', 0)}/{target.get('total_runs', 0)}"
        print(
            f"{target.get('target_id', '-'):32} "
            f"{target.get('database_host') or target.get('host') or '-':18} "
            f"{str(target.get('current_run') or '-'):38} "
            f"{str(target.get('current_phase') or target.get('status') or '-'):24} "
            f"{done}"
        )
