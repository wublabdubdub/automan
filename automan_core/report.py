from __future__ import annotations

from pathlib import Path
from typing import Any

from automan_core.config import load_yaml
from automan_core.executor import FATAL_OUTPUT_PATTERNS


DEFAULT_TEMPLATE = """# TPC-C Campaign Report

Campaign: {{ campaign_id }}
Status: {{ status }}
Manual parameter commands: `{{ manual_parameter_commands_path }}`

## Matrix

{{ matrix }}

## Targets

{{ targets }}

## Runs

{{ runs }}

## Failures

{{ failures }}
"""


def generate_report(root: Path, campaign_id: str) -> Path:
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    plan = load_yaml(campaign_dir / "resolved-plan.yaml")
    progress_path = campaign_dir / "progress.json"
    progress = load_yaml(progress_path) if progress_path.exists() else {}

    report_dir = campaign_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"

    context = {
        "campaign_id": str(plan.get("campaign_id", campaign_id)),
        "status": str(progress.get("status") or _load_status(campaign_dir)),
        "manual_parameter_commands_path": _manual_parameter_commands_path(campaign_dir, plan),
        "matrix": _render_matrix(plan.get("matrix", {})),
        "targets": _render_targets(plan.get("targets", [])),
        "runs": _render_runs(root, plan.get("runs", [])),
        "failures": _render_failures(root, plan.get("runs", []), progress),
    }
    report_path.write_text(_render_template(_load_template(root), context), encoding="utf-8")
    return report_path


def latest_campaign_id(root: Path) -> str | None:
    base = root / "runs" / "campaigns"
    if not base.exists():
        return None
    candidates = sorted((path for path in base.iterdir() if (path / "resolved-plan.yaml").exists()), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0].name if candidates else None


def _load_status(campaign_dir: Path) -> str:
    status_path = campaign_dir / "status.json"
    if not status_path.exists():
        return "unknown"
    return str(load_yaml(status_path).get("status", "unknown"))


def _manual_parameter_commands_path(campaign_dir: Path, plan: dict[str, Any]) -> str:
    archive = plan.get("archive", {})
    if isinstance(archive, dict) and archive.get("manual_parameter_commands_path"):
        return str(archive["manual_parameter_commands_path"])
    return str(campaign_dir / "manual-parameter-commands.sh")


def _render_matrix(matrix: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- warehouses: {', '.join(map(str, matrix.get('warehouses', [])))}",
            f"- terminals: {', '.join(map(str, matrix.get('terminals', [])))}",
            f"- load_workers: {matrix.get('load_workers', '-')}",
            f"- run_mins: {matrix.get('run_mins', '-')}",
        ]
    )


def _render_targets(targets: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for target in targets:
        connection = target.get("connection", {})
        if not isinstance(connection, dict):
            connection = {}
        lines.extend(
            [
                f"- {target.get('id', '-')}: {target.get('display_name', '-')}",
                f"  - database: {connection.get('db_host', '-')}:{connection.get('db_port', '-')}/{connection.get('db_name', '-')}",
                f"  - user: {connection.get('db_user', '-')}",
                f"  - password: {connection.get('db_password', '***')}",
                f"  - ddl_profile: {target.get('ddl_profile', '-')}",
                f"  - manual_parameter_commands: {target.get('manual_parameter_commands_path', '-')}",
            ]
        )
    return "\n".join(lines) if lines else "- none"


def _render_runs(root: Path, runs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for run in runs:
        status = _run_status(root, run)
        lines.append(
            "- "
            f"{run.get('run_id', '-')}: {status}; "
            f"target={run.get('target_id', '-')}; "
            f"w={run.get('warehouse', '-')}; "
            f"c={run.get('terminals', '-')}; "
            f"result={run.get('benchmark_result_dir', '-')}"
        )
    return "\n".join(lines) if lines else "- none"


def _render_failures(root: Path, runs: list[dict[str, Any]], progress: dict[str, Any]) -> str:
    snippets: list[str] = []
    if progress.get("last_error"):
        snippets.append(f"- campaign: {progress['last_error']}")
    for target in progress.get("targets", []):
        if target.get("last_error"):
            snippets.append(f"- {target.get('target_id', '-')}: {target['last_error']}")
    for run in runs:
        run_id = str(run.get("run_id", "-"))
        status = _load_run_status(root, run)
        if status.get("last_error"):
            snippets.append(f"- {run_id}: {status['last_error']}")
        snippets.extend(f"- {run_id}: {line}" for line in _fatal_log_lines(root, run))
    return "\n".join(dict.fromkeys(snippets)) if snippets else "- none"


def _run_status(root: Path, run: dict[str, Any]) -> str:
    status = _load_run_status(root, run)
    return str(status.get("status", "unknown"))


def _load_run_status(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    status_path = Path(str(run.get("status_path") or root / "runs" / str(run.get("run_id", "-")) / "status.json"))
    if not status_path.exists():
        return {}
    return load_yaml(status_path)


def _fatal_log_lines(root: Path, run: dict[str, Any]) -> list[str]:
    log_dir = Path(str(run.get("command_log_dir") or root / "runs" / str(run.get("run_id", "-")) / "logs"))
    if not log_dir.exists():
        return []
    lines: list[str] = []
    for path in sorted(log_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and any(pattern.search(stripped) for pattern in FATAL_OUTPUT_PATTERNS):
                lines.append(f"{path.name}: {stripped}")
    return lines


def _load_template(root: Path) -> str:
    template_path = root / "templates" / "report.md.j2"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return DEFAULT_TEMPLATE


def _render_template(template: str, context: dict[str, str]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
    return rendered
