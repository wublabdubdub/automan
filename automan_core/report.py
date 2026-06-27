from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from automan_core.config import load_yaml, write_json
from automan_core.executor import FATAL_OUTPUT_PATTERNS


DEFAULT_TEMPLATE = """# TPC-C Campaign Report

## Campaign

{{ campaign }}

## Matrix

{{ matrix }}

## Targets

{{ targets }}

## Benchmark Results

{{ benchmark_results }}

## Resource Artifacts

{{ resource_artifacts }}

## Collection Status

{{ collection_status }}

## Perf Record Artifacts

{{ perf_artifacts }}

## Failures

{{ failures }}
"""


RESULT_PATTERNS = {
    "measured_tpmc": re.compile(r"Measured\s+tpmC\s*\(NewOrders\)\s*=\s*([0-9]+(?:\.[0-9]+)?)"),
    "measured_tpmtotal": re.compile(r"Measured\s+tpmTOTAL\s*=\s*([0-9]+(?:\.[0-9]+)?)"),
    "session_start": re.compile(r"Session\s+Start\s*=\s*([^\r\n]+)"),
    "session_end": re.compile(r"Session\s+End\s*=\s*([^\r\n]+)"),
}

TEXT_ARTIFACT_SUFFIXES = {".csv", ".err", ".json", ".jsonl", ".log", ".md", ".out", ".report", ".script", ".txt"}


def generate_report(root: Path, campaign_id: str) -> Path:
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    plan = load_yaml(campaign_dir / "resolved-plan.yaml")
    progress_path = campaign_dir / "progress.json"
    progress = load_yaml(progress_path) if progress_path.exists() else {}

    report_dir = campaign_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"

    agent_context = _build_agent_context(root, campaign_dir, plan, progress)
    agent_context_path = report_dir / "agent-context.json"
    write_json(agent_context_path, agent_context)

    context = {
        "campaign": _render_campaign(campaign_dir, plan, progress, agent_context_path),
        "manual_parameter_commands_path": _manual_parameter_commands_path(campaign_dir, plan),
        "matrix": _render_matrix(plan.get("matrix", {})),
        "targets": _render_targets(plan.get("targets", [])),
        "benchmark_results": _render_benchmark_results(agent_context["parsed_results"]),
        "resource_artifacts": _render_artifacts(agent_context["artifact_paths"]["resource"], include_samples=True),
        "collection_status": _render_collection_status(agent_context["artifact_paths"].get("manifests", [])),
        "perf_artifacts": _render_artifacts(agent_context["artifact_paths"]["perf"], include_samples=False),
        "failures": _render_failures_from_context(agent_context["failures"]),
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


def _render_campaign(campaign_dir: Path, plan: dict[str, Any], progress: dict[str, Any], agent_context_path: Path) -> str:
    lines = [
        f"- campaign_id: {plan.get('campaign_id', campaign_dir.name)}",
        f"- status: {progress.get('status') or _load_status(campaign_dir)}",
        f"- benchmark: {plan.get('benchmark', '-')}",
        f"- manual_parameter_commands: `{_manual_parameter_commands_path(campaign_dir, plan)}`",
        f"- agent_context: `{agent_context_path}`",
    ]
    return "\n".join(lines)


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


def _render_benchmark_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "- none"
    lines = [
        "| Run | Status | Target | Warehouses | Terminals | Run Mins | Measured tpmC | Measured tpmTOTAL | Session Start | Session End | Elapsed Seconds | Result Dir |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(result.get("run_id")),
                    _cell(result.get("status")),
                    _cell(result.get("target_id")),
                    _cell(result.get("warehouse")),
                    _cell(result.get("terminals")),
                    _cell(result.get("run_mins")),
                    _cell(result.get("measured_tpmc")),
                    _cell(result.get("measured_tpmtotal")),
                    _cell(result.get("session_start")),
                    _cell(result.get("session_end")),
                    _cell(result.get("elapsed_seconds")),
                    f"`{_cell(result.get('benchmark_result_dir'))}`",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_artifacts(artifacts: list[dict[str, Any]], include_samples: bool) -> str:
    if not artifacts:
        return "- none"
    if include_samples:
        lines = [
            "| Run | Path | Size Bytes | Empty | Lines | Samples |",
            "| --- | --- | ---: | --- | ---: | ---: |",
        ]
    else:
        lines = [
            "| Run | Path | Size Bytes | Empty | Lines |",
            "| --- | --- | ---: | --- | ---: |",
        ]
    for artifact in artifacts:
        cells = [
            _cell(artifact.get("run_id")),
            f"`{_cell(artifact.get('path'))}`",
            _cell(artifact.get("size_bytes")),
            _cell(artifact.get("empty")),
            _cell(artifact.get("line_count")),
        ]
        if include_samples:
            cells.append(_cell(artifact.get("sample_count")))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_collection_status(manifests: list[dict[str, Any]]) -> str:
    if not manifests:
        return "- none"
    lines = [
        "| Run | Phase | Role | Host Type | Status | Started At | Ended At | Errors | Manifest |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for manifest in manifests:
        errors = manifest.get("errors") or []
        if isinstance(errors, list):
            error_text = "; ".join(str(item) for item in errors) if errors else "-"
        else:
            error_text = str(errors)
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(manifest.get("run_id")),
                    _cell(manifest.get("phase")),
                    _cell(manifest.get("role")),
                    _cell(manifest.get("host_type")),
                    _cell(manifest.get("status")),
                    _cell(manifest.get("started_at")),
                    _cell(manifest.get("ended_at")),
                    _cell(error_text),
                    f"`{_cell(manifest.get('path'))}`",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


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


def _render_failures_from_context(failures: list[str]) -> str:
    return "\n".join(f"- {failure}" for failure in failures) if failures else "- none"


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


def _build_agent_context(root: Path, campaign_dir: Path, plan: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    runs = _runs(plan)
    parsed_results = [_run_result(root, run) for run in runs]
    artifact_paths = _artifact_paths(root, runs)
    failures = _failure_lines(root, runs, progress)
    return {
        "plan": plan,
        "progress": progress,
        "parsed_results": parsed_results,
        "artifact_paths": artifact_paths,
        "collection_status": _collection_status(artifact_paths.get("manifests", [])),
        "failures": failures,
    }


def _runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    runs = plan.get("runs", [])
    return runs if isinstance(runs, list) else []


def _run_result(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    result = {
        "run_id": str(run.get("run_id", "-")),
        "target_id": str(run.get("target_id", "-")),
        "warehouse": run.get("warehouse", "-"),
        "terminals": run.get("terminals", "-"),
        "run_mins": run.get("run_mins", "-"),
        "status": _run_status(root, run),
        "benchmark_result_dir": str(_path_value(root, run, "benchmark_result_dir", "benchmark/result")),
        "measured_tpmc": None,
        "measured_tpmtotal": None,
        "session_start": None,
        "session_end": None,
        "elapsed_seconds": None,
        "source_paths": [],
        "command_results": _command_results(root, run),
    }
    _merge_result_text_sources(root, run, result)
    _merge_result_csv_sources(root, run, result)
    result["elapsed_seconds"] = _elapsed_seconds(result.get("session_start"), result.get("session_end"))
    return result


def _merge_result_text_sources(root: Path, run: dict[str, Any], result: dict[str, Any]) -> None:
    for path in _benchmark_text_sources(root, run):
        text = _read_text(path)
        if text is None:
            continue
        matched = False
        for key, pattern in RESULT_PATTERNS.items():
            for match in pattern.finditer(text):
                value: Any = match.group(1).strip()
                if key in {"measured_tpmc", "measured_tpmtotal"}:
                    value = _number(value)
                result[key] = value
                matched = True
        if matched:
            result["source_paths"].append(str(path))


def _merge_result_csv_sources(root: Path, run: dict[str, Any], result: dict[str, Any]) -> None:
    result_dir = _path_value(root, run, "benchmark_result_dir", "benchmark/result")
    tx_summary = result_dir / "data" / "tx_summary.csv"
    if tx_summary.exists():
        matched = _merge_tx_summary(tx_summary, result)
        if matched:
            result["source_paths"].append(str(tx_summary))
    run_info = result_dir / "data" / "runInfo.csv"
    if run_info.exists():
        matched = _merge_run_info(run_info, result)
        if matched:
            result["source_paths"].append(str(run_info))


def _merge_tx_summary(path: Path, result: dict[str, Any]) -> bool:
    matched = False
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                name = row[0].strip().lower()
                if name == "tpmc":
                    result["measured_tpmc"] = _number(row[1])
                    matched = True
                elif name in {"tpmtotal", "tpm_total"}:
                    result["measured_tpmtotal"] = _number(row[1])
                    matched = True
    except OSError:
        return False
    return matched


def _merge_run_info(path: Path, result: dict[str, Any]) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return False
    if not rows:
        return False
    row = rows[-1]
    if row.get("sessionStart") and not result.get("session_start"):
        result["session_start"] = row["sessionStart"].strip()
    if row.get("runMins") and result.get("run_mins") in {None, "-"}:
        result["run_mins"] = row["runMins"].strip()
    return bool(row.get("sessionStart") or row.get("runMins"))


def _benchmark_text_sources(root: Path, run: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    log_dir = _path_value(root, run, "command_log_dir", "logs")
    if log_dir.exists():
        paths.extend(sorted(log_dir.glob("*.log")))
    result_dir = _path_value(root, run, "benchmark_result_dir", "benchmark/result")
    if result_dir.exists():
        paths.extend(path for path in sorted(result_dir.rglob("*")) if path.is_file() and _is_text_artifact(path))
    return list(dict.fromkeys(paths))


def _command_results(root: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    log_dir = _path_value(root, run, "command_log_dir", "logs")
    results: list[dict[str, Any]] = []
    if not log_dir.exists():
        return results
    for path in sorted(log_dir.glob("*.result.json")):
        try:
            data = load_yaml(path)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["path"] = str(path)
            results.append(data)
    if results:
        return results
    jsonl = log_dir / "command-results.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                import json

                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                data["path"] = str(jsonl)
                results.append(data)
    return results


def _artifact_paths(root: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    resource: list[dict[str, Any]] = []
    perf: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    by_run: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for run in runs:
        run_id = str(run.get("run_id", "-"))
        run_dir = _path_value(root, run, "run_dir", "")
        collectors_dir = run_dir / "collectors"
        run_resource: list[dict[str, Any]] = []
        run_perf: list[dict[str, Any]] = []
        if collectors_dir.exists():
            for path in sorted(p for p in collectors_dir.rglob("*") if p.is_file()):
                if path.name == "manifest.json":
                    manifest = _manifest_summary(run_id, path)
                    manifests.append(manifest)
                    continue
                artifact = _artifact_summary(run_id, path)
                if _is_perf_artifact(path):
                    perf.append(artifact)
                    run_perf.append(artifact)
                elif _is_resource_artifact(path):
                    resource.append(artifact)
                    run_resource.append(artifact)
        by_run[run_id] = {"resource": run_resource, "perf": run_perf}
    return {"resource": resource, "perf": perf, "manifests": manifests, "by_run": by_run}


def _artifact_summary(run_id: str, path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    summary = {
        "run_id": run_id,
        "path": str(path),
        "size_bytes": size,
        "empty": size == 0,
        "line_count": None,
        "sample_count": None,
    }
    lines = _artifact_lines(path)
    if lines is not None:
        summary["line_count"] = len(lines)
        if _is_resource_artifact(path):
            summary["sample_count"] = _system_sample_count(path, lines)
    return summary


def _manifest_summary(run_id: str, path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        loaded = load_yaml(path)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    collectors = data.get("collectors", {}) if isinstance(data.get("collectors", {}), dict) else {}
    system = collectors.get("system", {}) if isinstance(collectors.get("system", {}), dict) else {}
    perf = collectors.get("perf", {}) if isinstance(collectors.get("perf", {}), dict) else {}
    return {
        "run_id": run_id,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "phase": data.get("phase", _phase_from_manifest_path(path)),
        "role": data.get("role", "-"),
        "host_type": data.get("host_type", "-"),
        "status": data.get("status", "unknown"),
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "errors": data.get("errors", []),
        "system_status": data.get("system_status") or system.get("status") or ("success" if data.get("include_system") else "disabled"),
        "perf_status": data.get("perf_status") or perf.get("status") or ("success" if data.get("include_perf") else "disabled"),
        "include_system": data.get("include_system"),
        "include_perf": data.get("include_perf"),
        "commands": data.get("commands", []),
        "command_results": data.get("command_results", []),
        "artifacts": data.get("artifacts", []),
    }


def _collection_status(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for manifest in manifests:
        item = {
            "phase": manifest.get("phase", "-"),
            "role": manifest.get("role", "-"),
            "status": manifest.get("status", "unknown"),
            "system_status": manifest.get("system_status", "unknown"),
            "perf_status": manifest.get("perf_status", "unknown"),
            "manifest_path": manifest.get("path", "-"),
        }
        by_run.setdefault(str(manifest.get("run_id", "-")), []).append(item)
    return {"manifests": manifests, "by_run": by_run}


def _phase_from_manifest_path(path: Path) -> str:
    parts = list(path.parts)
    if "collectors" in parts:
        index = parts.index("collectors")
        if len(parts) > index + 1:
            return parts[index + 1]
    return "-"


def _artifact_lines(path: Path) -> list[str] | None:
    if not _is_text_artifact(path):
        return None
    text = _read_text(path)
    if text is None:
        return None
    return text.splitlines()


def _system_sample_count(path: Path, lines: list[str]) -> int:
    name = path.name.lower()
    samples = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(("linux", "procs", "avg-cpu", "device", "time", "timestamp", "#")):
            continue
        if name.startswith("vmstat") and re.match(r"^[0-9\s-]+$", stripped):
            samples += 1
        elif re.search(r"\d", stripped):
            samples += 1
    return samples


def _is_text_artifact(path: Path) -> bool:
    return path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES or path.name.endswith((".script.txt", ".report.txt"))


def _is_perf_artifact(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return "perf" in parts or path.name.lower().startswith("perf.")


def _is_resource_artifact(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return "system" in parts or path.suffix.lower() == ".log"


def _path_value(root: Path, run: dict[str, Any], key: str, default_suffix: str) -> Path:
    run_id = str(run.get("run_id", "-"))
    default = root / "runs" / run_id
    if default_suffix:
        default = default / default_suffix
    raw = run.get(key)
    path = Path(str(raw)) if raw else default
    return path if path.is_absolute() else root / path


def _failure_lines(root: Path, runs: list[dict[str, Any]], progress: dict[str, Any]) -> list[str]:
    failures = _render_failures(root, runs, progress)
    if failures == "- none":
        return []
    return [line[2:] if line.startswith("- ") else line for line in failures.splitlines()]


def _number(value: Any) -> float | str:
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


def _elapsed_seconds(session_start: Any, session_end: Any) -> int | None:
    if not session_start or not session_end:
        return None
    start = _parse_datetime(str(session_start))
    end = _parse_datetime(str(session_end))
    if not start or not end:
        return None
    return int((end - start).total_seconds())


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value)
    return text.replace("|", "\\|")


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
