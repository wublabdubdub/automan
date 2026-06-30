from __future__ import annotations

import re
import shlex
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from automan_core.config import write_json
from automan_core.models import Target, TpchBackendConfig, TpchConfig, TpchRunSpec
from automan_core.remote import RemoteClient
from automan_core.ssh import CommandResult


RemoteFactory = Callable[[Target], RemoteClient]


def stage_flags(stage: str) -> dict[str, str]:
    if stage == "tpch-load":
        return {
            "RUN_COMPILE_TPCH": "true",
            "RUN_GEN_DATA": "true",
            "RUN_INIT": "true",
            "RUN_DDL": "true",
            "RUN_LOAD": "true",
            "RUN_SQL": "false",
            "RUN_SINGLE_USER_REPORT": "false",
            "RUN_MULTI_USER": "false",
            "RUN_MULTI_USER_REPORT": "false",
        }
    if stage == "tpch-query":
        return {
            "RUN_COMPILE_TPCH": "false",
            "RUN_GEN_DATA": "false",
            "RUN_INIT": "false",
            "RUN_DDL": "false",
            "RUN_LOAD": "false",
            "RUN_SQL": "true",
            "RUN_SINGLE_USER_REPORT": "true",
            "RUN_MULTI_USER": "false",
            "RUN_MULTI_USER_REPORT": "false",
        }
    raise ValueError(f"unsupported TPC-H stage: {stage}")


def render_mars3_storage(compress_threshold: int | None) -> str:
    if compress_threshold is None:
        return "USING mars3 with (compresstype=zstd, compresslevel=2)"
    return f"USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold={compress_threshold})"


def remote_backend_dir(target: Target, backend: TpchBackendConfig, run: TpchRunSpec) -> str:
    rendered = backend.remote_dir.format(run_id=run.run_id)
    if rendered.startswith("/"):
        return rendered.rstrip("/")
    base = target.connection.remote_workdir.rstrip("/") or "/tmp"
    return f"{base}/{rendered}".rstrip("/")


def render_backend_variables(target: Target, backend: TpchBackendConfig, run: TpchRunSpec) -> str:
    remote_dir = remote_backend_dir(target, backend, run)
    storage = render_mars3_storage(run.compress_threshold)
    variables: dict[str, str] = {
        "PGHOST": target.connection.db_host,
        "PGPORT": str(target.connection.db_port),
        "PGDATABASE": target.connection.db_name,
        "PGUSER": target.connection.db_user,
        "PGPASSWORD": target.connection.db_password,
        "REPO_URL": "https://github.com/ymatrix-data/TPC-H",
        "ADMIN_USER": target.connection.ssh_user,
        "INSTALL_DIR": remote_dir,
        "EXPLAIN_ANALYZE": _bool_text(backend.explain_analyze),
        "RANDOM_DISTRIBUTION": "false",
        "MULTI_USER_COUNT": "1",
        "GEN_DATA_SCALE": str(run.scale_factor),
        "SINGLE_USER_ITERATIONS": str(max(1, run.query_streams)),
        "GREENPLUM_PATH": backend.greenplum_path,
        "SMALL_STORAGE": storage,
        "MEDIUM_STORAGE": storage,
        "LARGE_STORAGE": storage,
        "OPTIMIZER": backend.optimizer,
        "GEN_DATA_DIR": f"{remote_dir}/generated",
        "EXT_HOST_DATA_DIR": f"{remote_dir}/ext",
        "ADD_FOREIGN_KEY": "false",
        "CREATE_TBL": "false",
        "PREHEATING_DATA": _bool_text(backend.preheating_data),
        "DATABASE_TYPE": backend.database_type,
        "LOAD_DATA_TYPE": backend.load_data_type,
        "TPCH_RUN_ID": _safe_run_id(run.run_id),
        "TPCH_SESSION_GUCS": "",
        "PURE_SCRIPT_MODE": "true",
    }
    variables.update(stage_flags(run.stage))
    lines = [f'export {name}="{_escape(value)}"' for name, value in variables.items() if name.startswith("PG")]
    lines.extend(f'{name}="{_escape(value)}"' for name, value in variables.items() if not name.startswith("PG"))
    return "\n".join(lines) + "\n"


def normalize_backend_result(
    run: TpchRunSpec,
    command_result: CommandResult,
    remote_dir: str,
    local_artifact_dir: Path,
    started: str,
    ended: str,
) -> dict[str, Any]:
    timings = _parse_sql_timings(local_artifact_dir)
    load_rows = _parse_log_rows(local_artifact_dir / "generated" / "log" / "rollout_load.log")
    elapsed = _elapsed_seconds(started, ended)
    error = _command_error(command_result)
    result: dict[str, Any] = {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "ddl_profile": run.ddl_profile,
        "compress_threshold": run.compress_threshold,
        "scale_factor": run.scale_factor,
        "query_streams": run.query_streams,
        "run_mins": run.run_mins,
        "backend_type": "ymatrix-tpch",
        "schema": "tpch",
        "remote_backend_dir": remote_dir,
        "upstream_artifacts": {"local_dir": str(local_artifact_dir)},
        "status": "success" if command_result.exit_code == 0 else "failed",
        "error": error,
        "session_start": started,
        "session_end": ended,
        "elapsed_seconds": elapsed,
        "result_dir": str(local_artifact_dir),
    }
    if run.stage == "tpch-load":
        result.update(
            {
                "loaded_tables": len(load_rows) if load_rows else None,
                "loaded_rows": sum(row["tuples"] for row in load_rows) if load_rows else None,
            }
        )
    if run.stage == "tpch-query":
        result.update(_latency_summary(timings))
        result["query_count"] = len(timings)
        result["errors"] = 0 if command_result.exit_code == 0 else 1
        result["queries_per_hour"] = round((len(timings) * 3600.0 / elapsed), 2) if timings and elapsed > 0 and command_result.exit_code == 0 else None
        result["qphh"] = result["queries_per_hour"]
    return result


class YMatrixTpchBackend:
    def __init__(self, remote_factory: RemoteFactory | None = None) -> None:
        self.remote_factory = remote_factory

    def run(self, root: Path, target: Target, config: TpchConfig, run: TpchRunSpec) -> dict[str, Any]:
        backend = config.backend
        source_dir = _resolve_root_path(root, backend.source_dir)
        started = datetime.now().isoformat()
        remote_dir = remote_backend_dir(target, backend, run)
        artifact_dir = run.benchmark_dir / "upstream"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if not source_dir.exists():
            ended = datetime.now().isoformat()
            return _failed_backend_result(run, remote_dir, artifact_dir, started, ended, f"YMatrix TPC-H backend source not found: {source_dir}")

        client = self.remote_factory(target) if self.remote_factory is not None else _default_remote_client(target, backend)
        setup = client.run(f"rm -rf {shlex.quote(remote_dir)} && mkdir -p {shlex.quote(remote_dir)}", timeout=300)
        _write_command(run.logs_dir, "ymatrix-backend-setup", setup)
        if setup.exit_code != 0:
            ended = datetime.now().isoformat()
            return _failed_backend_result(run, remote_dir, artifact_dir, started, ended, _command_error(setup) or "remote backend setup failed")

        archive = run.logs_dir / "ymatrix-tpch.tar.gz"
        _write_source_archive(source_dir, archive)
        remote_archive = f"{remote_dir}/ymatrix-tpch.tar.gz"
        upload = client.upload_file(archive, remote_archive)
        _write_command(run.logs_dir, "ymatrix-backend-upload", upload)
        if upload.exit_code != 0:
            ended = datetime.now().isoformat()
            return _failed_backend_result(run, remote_dir, artifact_dir, started, ended, _command_error(upload) or "remote backend upload failed")

        extract = client.run(f"cd {shlex.quote(remote_dir)} && tar -xzf ymatrix-tpch.tar.gz && rm -f ymatrix-tpch.tar.gz", timeout=600)
        _write_command(run.logs_dir, "ymatrix-backend-extract", extract)
        if extract.exit_code != 0:
            ended = datetime.now().isoformat()
            return _failed_backend_result(run, remote_dir, artifact_dir, started, ended, _command_error(extract) or "remote backend extract failed")

        variables = render_backend_variables(target, backend, run)
        local_variables = run.logs_dir / "tpch_variables.sh"
        local_variables.write_text(variables, encoding="utf-8")
        upload_vars = client.upload_file(local_variables, f"{remote_dir}/tpch_variables.sh")
        _write_command(run.logs_dir, "ymatrix-backend-upload-vars", upload_vars)
        if upload_vars.exit_code != 0:
            ended = datetime.now().isoformat()
            return _failed_backend_result(run, remote_dir, artifact_dir, started, ended, _command_error(upload_vars) or "remote variable upload failed")

        command = (
            f"cd {shlex.quote(remote_dir)} && "
            "chmod +x ./tpch.sh ./rollout.sh && "
            "bash ./tpch.sh"
        )
        run_result = client.run(command, timeout=172800)
        _write_command(run.logs_dir, "ymatrix-backend-run", run_result)
        fetch = client.download_dir(remote_dir, artifact_dir)
        _write_command(run.logs_dir, "ymatrix-backend-fetch", fetch)
        ended = datetime.now().isoformat()
        result = normalize_backend_result(run, run_result, remote_dir, artifact_dir, started, ended)
        if fetch.exit_code != 0:
            result["artifact_fetch_error"] = _command_error(fetch)
            if result["status"] == "success":
                result["status"] = "failed"
                result["error"] = result["artifact_fetch_error"]
        write_json(run.database_dir / "ymatrix-backend-result.json", result)
        return result


def _default_remote_client(target: Target, backend: TpchBackendConfig) -> RemoteClient:
    connection = target.connection
    return RemoteClient(
        host=connection.db_host,
        port=connection.ssh_port,
        user=connection.ssh_user,
        password=connection.ssh_password,
    )


def _failed_backend_result(run: TpchRunSpec, remote_dir: str, artifact_dir: Path, started: str, ended: str, error: str) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "target_id": run.target_id,
        "stage": run.stage,
        "ddl_profile": run.ddl_profile,
        "compress_threshold": run.compress_threshold,
        "scale_factor": run.scale_factor,
        "query_streams": run.query_streams,
        "run_mins": run.run_mins,
        "backend_type": "ymatrix-tpch",
        "schema": "tpch",
        "remote_backend_dir": remote_dir,
        "upstream_artifacts": {"local_dir": str(artifact_dir)},
        "status": "failed",
        "error": error,
        "session_start": started,
        "session_end": ended,
        "elapsed_seconds": _elapsed_seconds(started, ended),
        "result_dir": str(artifact_dir),
    }


def _write_command(logs_dir: Path, name: str, result: CommandResult) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    (logs_dir / f"{safe}.cmd").write_text(result.command, encoding="utf-8")
    (logs_dir / f"{safe}.stdout").write_text(result.stdout, encoding="utf-8")
    (logs_dir / f"{safe}.stderr").write_text(result.stderr, encoding="utf-8")


def _write_source_archive(source_dir: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(source_dir.rglob("*")):
            tar.add(path, arcname=path.relative_to(source_dir).as_posix())


def _parse_sql_timings(artifact_dir: Path) -> list[float]:
    rows = _parse_log_rows(artifact_dir / "generated" / "log" / "rollout_sql.log")
    return [row["elapsed_ms"] for row in rows]


def _parse_log_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        rows.append(
            {
                "id": parts[0],
                "name": parts[1],
                "tuples": _safe_int(parts[2]),
                "elapsed_ms": _duration_to_ms(parts[3]),
            }
        )
    return rows


def _latency_summary(timings: list[float]) -> dict[str, float | None]:
    if not timings:
        return {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    ordered = sorted(timings)
    return {
        "avg_ms": round(sum(ordered) / len(ordered), 3),
        "p50_ms": round(_percentile(ordered, 0.50), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "p99_ms": round(_percentile(ordered, 0.99), 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = int(round((len(values) - 1) * percentile))
    return values[max(0, min(index, len(values) - 1))]


def _duration_to_ms(value: str) -> float:
    match = re.match(r"^(\d+):(\d+):(\d+)\.(\d+)$", value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return float(((hours * 3600) + (minutes * 60) + seconds) * 1000 + millis)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def _command_error(result: CommandResult) -> str | None:
    if result.exit_code == 0:
        return None
    return (result.stderr or result.stdout or f"command failed with exit code {result.exit_code}").strip()


def _elapsed_seconds(started: str, ended: str) -> float:
    try:
        return round((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds(), 6)
    except ValueError:
        return 0.0


def _resolve_root_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path
