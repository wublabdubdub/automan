from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from automan_core.config import load_yaml, write_json
from automan_core.models import RunSpec, Target


RESULT_PATTERNS = {
    "measured_tpmc": re.compile(r"Measured\s+tpmC\s*\(NewOrders\)\s*=\s*([0-9]+(?:\.[0-9]+)?)"),
    "measured_tpmtotal": re.compile(r"Measured\s+tpmTOTAL\s*=\s*([0-9]+(?:\.[0-9]+)?)"),
    "session_start": re.compile(r"Session\s+Start\s*=\s*([^\r\n]+)"),
    "session_end": re.compile(r"Session\s+End\s*=\s*([^\r\n]+)"),
}
TEXT_ARTIFACT_SUFFIXES = {".csv", ".err", ".json", ".jsonl", ".log", ".md", ".out", ".report", ".script", ".txt"}
MAX_METRIC_TEXT_BYTES = 1024 * 1024


def publish_run_result(root: Path, target: Target, run: RunSpec) -> Path:
    run_dict = {
        "run_id": run.run_id,
        "target_id": target.id,
        "warehouse": run.warehouse,
        "terminals": run.terminals,
        "run_mins": run.run_mins,
        "run_dir": str(root / "runs" / run.run_id),
        "status_path": str(root / "runs" / run.run_id / "status.json"),
        "command_log_dir": str(root / "runs" / run.run_id / "logs"),
        "benchmark_result_dir": str(root / "runs" / run.run_id / "benchmark" / "result"),
    }
    result = build_run_result(root, run_dict)
    path = run_result_path(root, run_dict)
    write_json(path, result)
    return path


def load_published_run_result(root: Path, run: dict[str, Any]) -> dict[str, Any] | None:
    path = run_result_path(root, run)
    if not path.exists():
        return None
    loaded = load_yaml(path)
    return loaded if isinstance(loaded, dict) else None


def run_result_path(root: Path, run: dict[str, Any]) -> Path:
    return _path_value(root, run, "run_dir", "") / "result.json"


def build_run_result(root: Path, run: dict[str, Any]) -> dict[str, Any]:
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
        "source_paths": [],
    }
    _merge_result_text_sources(root, run, result)
    _merge_result_csv_sources(root, run, result)
    return result


def _run_status(root: Path, run: dict[str, Any]) -> str:
    status_path = _path_value(root, run, "status_path", "status.json")
    if not status_path.exists():
        return "unknown"
    loaded = load_yaml(status_path)
    if not isinstance(loaded, dict):
        return "unknown"
    return str(loaded.get("status", "unknown"))


def _merge_result_text_sources(root: Path, run: dict[str, Any], result: dict[str, Any]) -> None:
    for path in _benchmark_text_sources(root, run):
        text = _read_metric_text(path)
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
    if tx_summary.exists() and _merge_tx_summary(tx_summary, result):
        result["source_paths"].append(str(tx_summary))
    run_info = result_dir / "data" / "runInfo.csv"
    if run_info.exists() and _merge_run_info(run_info, result):
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
        benchmark_log = log_dir / "runBenchmark.sh.stdout.log"
        if benchmark_log.exists():
            paths.append(benchmark_log)
        else:
            paths.extend(sorted(log_dir.glob("*Benchmark*.stdout.log")))
    result_dir = _path_value(root, run, "benchmark_result_dir", "benchmark/result")
    if result_dir.exists():
        paths.extend(path for path in sorted(result_dir.rglob("*")) if path.is_file() and _is_metric_text_artifact(path))
    return list(dict.fromkeys(paths))


def _path_value(root: Path, run: dict[str, Any], key: str, default_suffix: str) -> Path:
    run_id = str(run.get("run_id", "-"))
    default = root / "runs" / run_id
    if default_suffix:
        default = default / default_suffix
    raw = run.get(key)
    path = Path(str(raw)) if raw else default
    return path if path.is_absolute() else root / path


def _is_text_artifact(path: Path) -> bool:
    return path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES or path.name.endswith((".script.txt", ".report.txt"))


def _is_metric_text_artifact(path: Path) -> bool:
    return path.suffix.lower() != ".csv" and _is_text_artifact(path)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_metric_text(path: Path) -> str | None:
    try:
        size = path.stat().st_size
        if size <= MAX_METRIC_TEXT_BYTES:
            return path.read_text(encoding="utf-8", errors="replace")
        with path.open("rb") as f:
            f.seek(-MAX_METRIC_TEXT_BYTES, 2)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _number(value: Any) -> float | str:
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text
