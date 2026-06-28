from __future__ import annotations

import json
import posixpath
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import paramiko

from automan_core.models import RunSpec, Target


SYSTEM_PHASES = {"runDatabaseBuild.sh", "runBenchmark.sh"}
DEFAULT_PERF_PHASES = {"runBenchmark.sh"}
REMOTE_FETCH_MAX_BYTES = 128 * 1024 * 1024


class CollectorError(RuntimeError):
    pass


class NullCollectorManager:
    def start_phase(self, phase: str) -> None:
        return None

    def stop_phase(self, phase: str) -> None:
        return None


class CollectorManager:
    def __init__(
        self,
        root: Path,
        target: Target,
        run: RunSpec,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.root = root
        self.target = target
        self.run = run
        self.config = config or {}
        self.system_interval = int(self._section("system").get("interval_seconds", 1))
        self.system_tools = set(str(tool) for tool in self._section("system").get("tools", ["vmstat", "iostat", "pidstat", "mpstat"]))
        self.system_host_roles = set(str(role) for role in self._section("system").get("host_roles", ["database"]))
        self.perf_frequency = int(
            self._section("perf").get("frequency", self._section("perf").get("freq", 99))
        )
        self.perf_phases = set(str(phase) for phase in self._section("perf").get("phases", list(DEFAULT_PERF_PHASES)))
        self.perf_host_roles = set(str(role) for role in self._section("perf").get("host_roles", ["database"]))
        self.perf_call_graph = str(self._section("perf").get("call_graph", "fp"))
        self.perf_mode = str(self._section("perf").get("mode", "sampled")).lower()
        self.perf_sample_count = int(self._section("perf").get("sample_count", 3))
        self.perf_sample_duration_seconds = int(self._section("perf").get("sample_duration_seconds", 60))
        self.perf_sample_delay_seconds = _optional_int(self._section("perf").get("sample_delay_seconds"))
        self.perf_sample_interval_seconds = _optional_int(self._section("perf").get("sample_interval_seconds"))
        self.perf_sample_delay_ratio = float(self._section("perf").get("sample_delay_ratio", 0.2))
        self.perf_sample_interval_ratio = float(self._section("perf").get("sample_interval_ratio", 0.3))
        self.perf_samples = _build_perf_samples(
            mode=self.perf_mode,
            run_mins=run.run_mins,
            count=self.perf_sample_count,
            duration_seconds=self.perf_sample_duration_seconds,
            delay_seconds=self.perf_sample_delay_seconds,
            interval_seconds=self.perf_sample_interval_seconds,
            delay_ratio=self.perf_sample_delay_ratio,
            interval_ratio=self.perf_sample_interval_ratio,
        )
        self.run_dir = root / "runs" / run.run_id
        self._active: dict[str, list[_HostCollector]] = {}

    def start_phase(self, phase: str) -> None:
        collectors = self._collectors_for_phase(phase)
        started: list[_HostCollector] = []
        try:
            for collector in collectors:
                collector.start(phase)
                started.append(collector)
        except Exception as exc:
            for collector in reversed(started):
                try:
                    collector.stop(phase)
                except Exception:
                    pass
            raise CollectorError(f"collector start failed for {phase}: {exc}") from exc
        self._active[phase] = started

    def stop_phase(self, phase: str) -> None:
        collectors = self._active.pop(phase, [])
        errors = []
        for collector in reversed(collectors):
            try:
                collector.stop(phase)
            except Exception as exc:
                errors.append(str(exc))
                try:
                    collector.cleanup(phase)
                except Exception as cleanup_exc:
                    errors.append(f"cleanup failed: {cleanup_exc}")
        if errors:
            raise CollectorError(f"collector stop failed for {phase}: {'; '.join(errors)}")

    def _collectors_for_phase(self, phase: str) -> list["_HostCollector"]:
        host_collectors: list[_HostCollector] = []
        for role in self._host_roles():
            include_system = self._enabled("system") and phase in SYSTEM_PHASES and role.name in self.system_host_roles
            include_perf = self._enabled("perf") and phase in self.perf_phases and role.name in self.perf_host_roles
            if not include_system and not include_perf:
                continue
            local_dir = self.run_dir / "collectors" / phase / role.name
            if role.is_local:
                host_collectors.append(
                    _LocalHostCollector(
                        role.name,
                        local_dir,
                        include_system=include_system,
                        include_perf=include_perf,
                        system_interval=self.system_interval,
                        system_tools=self.system_tools,
                        perf_frequency=self.perf_frequency,
                        perf_call_graph=self.perf_call_graph,
                        perf_mode=self.perf_mode,
                        perf_samples=self.perf_samples,
                    )
                )
            else:
                host_collectors.append(
                    _RemoteHostCollector(
                        role.name,
                        local_dir,
                        host=role.host,
                        port=role.port,
                        user=role.user,
                        password=role.password,
                        remote_base_dir=role.remote_base_dir,
                        run_id=self.run.run_id,
                        include_system=include_system,
                        include_perf=include_perf,
                        system_interval=self.system_interval,
                        system_tools=self.system_tools,
                        perf_frequency=self.perf_frequency,
                        perf_call_graph=self.perf_call_graph,
                        perf_mode=self.perf_mode,
                        perf_samples=self.perf_samples,
                    )
                )
        return host_collectors

    def _section(self, name: str) -> dict[str, Any]:
        section = self.config.get(name, {})
        return section if isinstance(section, dict) else {}

    def _enabled(self, name: str) -> bool:
        return bool(self._section(name).get("enabled", True))

    def _host_roles(self) -> list["_HostRole"]:
        local_markers = _local_host_markers()
        connection = self.target.connection
        db_host = connection.ssh_host or connection.db_host
        db_is_local = db_host in local_markers or connection.db_host in local_markers
        desired_roles = self.system_host_roles | self.perf_host_roles
        roles = []
        if "database" in desired_roles:
            roles.append(
                _HostRole(
                    name="database",
                    is_local=db_is_local,
                    host=db_host,
                    port=connection.ssh_port,
                    user=connection.ssh_user,
                    password=connection.ssh_password,
                    remote_base_dir=connection.remote_workdir,
                )
            )
        execution_host = connection.execution_host
        if "execution" in desired_roles and execution_host and execution_host in local_markers:
            roles.append(
                _HostRole(
                    name="execution",
                    is_local=True,
                    host=execution_host,
                    port=connection.execution_port,
                    user=connection.execution_user,
                    password=connection.execution_password,
                    remote_base_dir=connection.execution_workdir,
                )
            )
        return roles


@dataclass(frozen=True)
class _PerfSample:
    name: str
    delay_seconds: int
    duration_seconds: int

    @property
    def data_file(self) -> str:
        return f"{self.name}.data"

    @property
    def script_file(self) -> str:
        return f"{self.name}.script.txt"

    @property
    def report_file(self) -> str:
        return f"{self.name}.report.txt"


@dataclass(frozen=True)
class _HostRole:
    name: str
    is_local: bool
    host: str
    port: int
    user: str
    password: str
    remote_base_dir: str


class _HostCollector:
    def start(self, phase: str) -> None:
        raise NotImplementedError

    def stop(self, phase: str) -> None:
        raise NotImplementedError

    def cleanup(self, phase: str) -> None:
        return None


@dataclass
class _LocalProcess:
    name: str
    process: subprocess.Popen
    stdout_handle: Any
    stderr_handle: Any
    stop_signal: signal.Signals


class _LocalHostCollector(_HostCollector):
    def __init__(
        self,
        role: str,
        output_dir: Path,
        *,
        include_system: bool,
        include_perf: bool,
        system_interval: int,
        system_tools: set[str],
        perf_frequency: int,
        perf_call_graph: str,
        perf_mode: str = "continuous",
        perf_samples: list[_PerfSample] | None = None,
    ) -> None:
        self.role = role
        self.output_dir = output_dir
        self.system_dir = output_dir / "system"
        self.perf_dir = output_dir / "perf"
        self.include_system = include_system
        self.include_perf = include_perf
        self.system_interval = system_interval
        self.system_tools = system_tools
        self.perf_frequency = perf_frequency
        self.perf_call_graph = perf_call_graph
        self.perf_mode = perf_mode
        self.perf_samples = perf_samples or []
        self.processes: list[_LocalProcess] = []
        self.started_at: str | None = None
        self.commands: list[dict[str, Any]] = []
        self.command_results: list[dict[str, Any]] = []

    def start(self, phase: str) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = _now()
        self.commands = []
        self.command_results = []
        for name, command, stop_signal, output_dir in self._commands():
            self.commands.append({"name": name, "command": command, "output_dir": str(output_dir)})
            output_dir.mkdir(parents=True, exist_ok=True)
            if name == "perf-record":
                self._write_perf_samples()
            stdout = (output_dir / f"{name}.log").open("w", encoding="utf-8")
            stderr = (output_dir / f"{name}.stderr.log").open("w", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    cwd=output_dir,
                )
            except Exception:
                stdout.close()
                stderr.close()
                raise
            self.processes.append(_LocalProcess(name, process, stdout, stderr, stop_signal))
        self._write_manifest(phase, "running")

    def stop(self, phase: str) -> None:
        errors = []
        try:
            for item in reversed(self.processes):
                if item.process.poll() is None:
                    item.process.send_signal(item.stop_signal)
                try:
                    item.process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    item.process.kill()
                    item.process.wait(timeout=10)
                    errors.append(f"{item.name} did not stop cleanly")
                finally:
                    self.command_results.append({"name": item.name, "exit_code": item.process.poll()})
                    item.stdout_handle.close()
                    item.stderr_handle.close()
            self.processes = []
            if self.include_perf:
                errors.extend(self._export_perf())
        finally:
            self._write_manifest(phase, "failed" if errors else "success", errors)
        if errors:
            raise CollectorError(f"{self.role}: {'; '.join(errors)}")

    def _commands(self) -> list[tuple[str, list[str], signal.Signals, Path]]:
        commands: list[tuple[str, list[str], signal.Signals, Path]] = []
        interval = str(self.system_interval)
        if self.include_system:
            system_commands = {
                "vmstat": ("vmstat", ["vmstat", interval]),
                "iostat": ("iostat-x", ["iostat", "-x", interval]),
                "pidstat": ("pidstat-durh", ["pidstat", "-durh", interval]),
                "mpstat": ("mpstat-P-ALL", ["mpstat", "-P", "ALL", interval]),
            }
            for tool in sorted(self.system_tools):
                if tool not in system_commands:
                    raise CollectorError(f"unsupported system collector tool: {tool}")
                name, command = system_commands[tool]
                commands.append((name, command, signal.SIGTERM, self.system_dir))
        if self.include_perf:
            if self._sampled_perf_enabled():
                perf_command = ["sh", "-c", self._sampled_perf_shell_script(self.perf_dir)]
            else:
                perf_command = self._perf_record_command(str(self.perf_dir / "perf.data"), 86400)
            commands.append(
                (
                    "perf-record",
                    perf_command,
                    signal.SIGINT,
                    self.perf_dir,
                )
            )
        return commands

    def _export_perf(self) -> list[str]:
        if not self._sampled_perf_enabled():
            perf_data = self.perf_dir / "perf.data"
            if not perf_data.exists():
                return ["perf.data was not created"]
            return [
                error
                for error in (
                    self._run_export(["perf", "script", "-i", str(perf_data)], "perf.script.txt"),
                    self._run_export(["perf", "report", "--stdio", "-i", str(perf_data)], "perf.report.txt"),
                )
                if error
            ]

        data_files = [self.perf_dir / sample.data_file for sample in self.perf_samples if (self.perf_dir / sample.data_file).exists()]
        if not data_files:
            return ["perf sample data was not created"]
        errors = []
        for data_file in data_files:
            stem = data_file.stem
            errors.extend(
                error
                for error in (
                    self._run_export(["perf", "script", "-i", str(data_file)], f"{stem}.script.txt"),
                    self._run_export(["perf", "report", "--stdio", "-i", str(data_file)], f"{stem}.report.txt"),
                )
                if error
            )
        if not errors:
            self._copy_first_sample_to_legacy_names(data_files[0])
        return errors

    def _sampled_perf_enabled(self) -> bool:
        return self.perf_mode == "sampled" and bool(self.perf_samples)

    def _perf_record_command(self, data_path: str, duration_seconds: int) -> list[str]:
        command = [
            "perf",
            "record",
            "-F",
            str(self.perf_frequency),
            "-a",
            "-o",
            data_path,
        ]
        command.extend(_call_graph_args(self.perf_call_graph))
        command.extend(["--", "sleep", str(duration_seconds)])
        return command

    def _sampled_perf_shell_script(self, perf_dir: Path) -> str:
        lines = [
            "set -eu",
            "child=",
            "cleanup() { if [ -n \"${child:-}\" ]; then kill -INT \"$child\" 2>/dev/null || true; wait \"$child\" 2>/dev/null || true; fi; exit 0; }",
            "trap cleanup INT TERM",
            "run_child() { \"$@\" & child=$!; wait \"$child\"; child=; }",
            "elapsed=0",
        ]
        for sample in self.perf_samples:
            sleep_seconds = max(0, sample.delay_seconds)
            lines.extend(
                [
                    f"wait_seconds=$(( {sleep_seconds} - elapsed ))",
                    "if [ \"$wait_seconds\" -gt 0 ]; then run_child sleep \"$wait_seconds\"; elapsed=$((elapsed + wait_seconds)); fi",
                ]
            )
            command = self._perf_record_command(str(perf_dir / sample.data_file), sample.duration_seconds)
            lines.append("run_child " + " ".join(shlex.quote(part) for part in command))
            lines.append(f"elapsed=$((elapsed + {sample.duration_seconds}))")
        return "\n".join(lines)

    def _write_perf_samples(self) -> None:
        if not self._sampled_perf_enabled():
            return
        payload = _perf_samples_manifest(self.perf_samples)
        self.perf_dir.mkdir(parents=True, exist_ok=True)
        (self.perf_dir / "samples.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _copy_first_sample_to_legacy_names(self, data_file: Path) -> None:
        stem = data_file.stem
        shutil.copy2(data_file, self.perf_dir / "perf.data")
        for source_name, target_name in (
            (f"{stem}.script.txt", "perf.script.txt"),
            (f"{stem}.report.txt", "perf.report.txt"),
        ):
            source = self.perf_dir / source_name
            if source.exists():
                shutil.copy2(source, self.perf_dir / target_name)

    def _run_export(self, command: list[str], output_name: str) -> str | None:
        output_path = self.perf_dir / output_name
        stderr_path = self.perf_dir / f"{output_name}.stderr.log"
        with output_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, text=True, check=False)
        self.command_results.append(
            {
                "name": output_name,
                "command": command,
                "exit_code": completed.returncode,
                "stdout_path": str(output_path),
                "stderr_path": str(stderr_path),
            }
        )
        if completed.returncode != 0:
            return f"{' '.join(command)} exited {completed.returncode}"
        return None

    def _write_manifest(self, phase: str, status: str, errors: list[str] | None = None) -> None:
        artifacts = _artifact_index(self.output_dir)
        system_files = [item["relative_path"] for item in artifacts if str(item["relative_path"]).startswith("system/")]
        perf_files = [item["relative_path"] for item in artifacts if str(item["relative_path"]).startswith("perf/")]
        manifest = {
            "phase": phase,
            "role": self.role,
            "host_type": "local",
            "status": status,
            "started_at": self.started_at,
            "ended_at": _now() if status != "running" else None,
            "include_system": self.include_system,
            "include_perf": self.include_perf,
            "system_interval_seconds": self.system_interval,
            "system_tools": sorted(self.system_tools),
            "perf_frequency": self.perf_frequency,
            "perf_call_graph": self.perf_call_graph,
            "perf_mode": self.perf_mode,
            "perf_samples": _perf_samples_manifest(self.perf_samples) if self._sampled_perf_enabled() else [],
            "commands": self.commands,
            "command_results": self.command_results,
            "errors": errors or [],
            "collectors": {
                "system": {
                    "enabled": self.include_system,
                    "status": _component_status(self.include_system, status, errors),
                    "files": system_files,
                    "errors": errors or [] if self.include_system else [],
                },
                "perf": {
                    "enabled": self.include_perf,
                    "status": _component_status(self.include_perf, status, errors),
                    "files": perf_files,
                    "errors": errors or [] if self.include_perf else [],
                },
            },
            "artifacts": artifacts,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class _RemoteHostCollector(_HostCollector):
    def __init__(
        self,
        role: str,
        output_dir: Path,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        remote_base_dir: str,
        run_id: str,
        include_system: bool,
        include_perf: bool,
        system_interval: int,
        system_tools: set[str],
        perf_frequency: int,
        perf_call_graph: str,
        perf_mode: str = "continuous",
        perf_samples: list[_PerfSample] | None = None,
    ) -> None:
        self.role = role
        self.output_dir = output_dir
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.remote_dir = posixpath.join(
            remote_base_dir,
            ".automan_collectors",
            _safe_path_part(run_id),
            _safe_path_part(role),
        )
        self.include_system = include_system
        self.include_perf = include_perf
        self.system_interval = system_interval
        self.system_tools = system_tools
        self.perf_frequency = perf_frequency
        self.perf_call_graph = perf_call_graph
        self.perf_mode = perf_mode
        self.perf_samples = perf_samples or []
        self.started_at: str | None = None
        self.commands: list[dict[str, Any]] = []
        self.command_results: list[dict[str, Any]] = []

    def start(self, phase: str) -> None:
        phase_dir = self._phase_dir(phase)
        self.started_at = _now()
        self.commands = []
        self.command_results = []
        client = self._client()
        try:
            self._run_checked(client, f"mkdir -p {shlex.quote(posixpath.join(phase_dir, 'system'))} {shlex.quote(posixpath.join(phase_dir, 'perf'))}")
            if self.include_perf:
                self._write_remote_perf_samples(client, posixpath.join(phase_dir, "perf"))
            for name, command, _, output_dir in self._commands(phase_dir):
                self.commands.append({"name": name, "command": command, "remote_output_dir": output_dir})
                tool = "perf" if name == "perf-record" else command[0]
                self._run_checked(client, f"command -v {shlex.quote(tool)} >/dev/null 2>&1")
                self._run_checked(client, self._background_command(command, output_dir, name))
        finally:
            client.close()
        self._write_manifest(phase, "running")

    def stop(self, phase: str) -> None:
        phase_dir = self._phase_dir(phase)
        client = self._client()
        try:
            errors = []
            for name, _, sig, output_dir in reversed(self._commands(phase_dir)):
                result = self._run(client, self._stop_command(output_dir, name, sig), timeout=45)
                self.command_results.append({"name": name, "command": "stop", "exit_code": result[0], "stdout": result[1], "stderr": result[2]})
                if result[0] != 0:
                    errors.append(f"{name}: {result[2].strip() or result[1].strip() or result[0]}")
            if self.include_perf:
                perf_dir = posixpath.join(phase_dir, "perf")
                for name, command in self._remote_perf_export_commands(perf_dir):
                    code, out, err = self._run(client, command, timeout=300)
                    self.command_results.append({"name": name, "command": command, "exit_code": code, "stdout": out, "stderr": err})
                    if code != 0:
                        errors.append(f"{command}: {err.strip() or out.strip() or code}")
            self._fetch_tree(client, phase_dir, self.output_dir)
        finally:
            client.close()
        self._write_manifest(phase, "failed" if errors else "success", errors)
        if errors:
            raise CollectorError(f"{self.role}: {'; '.join(errors)}")

    def cleanup(self, phase: str) -> None:
        phase_dir = self._phase_dir(phase)
        client = self._client()
        try:
            for name, _, _, output_dir in reversed(self._commands(phase_dir)):
                self._run(client, self._force_stop_command(output_dir, name), timeout=45)
        finally:
            client.close()

    def _phase_dir(self, phase: str) -> str:
        return posixpath.join(self.remote_dir, _safe_path_part(phase))

    def _commands(self, phase_dir: str) -> list[tuple[str, list[str], str, str]]:
        commands: list[tuple[str, list[str], str, str]] = []
        interval = str(self.system_interval)
        system_dir = posixpath.join(phase_dir, "system")
        perf_dir = posixpath.join(phase_dir, "perf")
        if self.include_system:
            system_commands = {
                "vmstat": ("vmstat", ["vmstat", interval]),
                "iostat": ("iostat-x", ["iostat", "-x", interval]),
                "pidstat": ("pidstat-durh", ["pidstat", "-durh", interval]),
                "mpstat": ("mpstat-P-ALL", ["mpstat", "-P", "ALL", interval]),
            }
            for tool in sorted(self.system_tools):
                if tool not in system_commands:
                    raise CollectorError(f"unsupported system collector tool: {tool}")
                name, command = system_commands[tool]
                commands.append((name, command, "TERM", system_dir))
        if self.include_perf:
            if self._sampled_perf_enabled():
                perf_command = ["sh", "-c", self._sampled_perf_shell_script(perf_dir)]
            else:
                perf_command = self._perf_record_command(posixpath.join(perf_dir, "perf.data"), 86400)
            commands.append(
                (
                    "perf-record",
                    perf_command,
                    "INT",
                    perf_dir,
                )
            )
        return commands

    def _sampled_perf_enabled(self) -> bool:
        return self.perf_mode == "sampled" and bool(self.perf_samples)

    def _perf_record_command(self, data_path: str, duration_seconds: int) -> list[str]:
        command = [
            "perf",
            "record",
            "-F",
            str(self.perf_frequency),
            "-a",
            "-o",
            data_path,
        ]
        command.extend(_call_graph_args(self.perf_call_graph))
        command.extend(["--", "sleep", str(duration_seconds)])
        return command

    def _sampled_perf_shell_script(self, perf_dir: str) -> str:
        lines = [
            "set -eu",
            "child=",
            "cleanup() { if [ -n \"${child:-}\" ]; then kill -INT \"$child\" 2>/dev/null || true; wait \"$child\" 2>/dev/null || true; fi; exit 0; }",
            "trap cleanup INT TERM",
            "run_child() { \"$@\" & child=$!; wait \"$child\"; child=; }",
            "elapsed=0",
        ]
        for sample in self.perf_samples:
            sleep_seconds = max(0, sample.delay_seconds)
            lines.extend(
                [
                    f"wait_seconds=$(( {sleep_seconds} - elapsed ))",
                    "if [ \"$wait_seconds\" -gt 0 ]; then run_child sleep \"$wait_seconds\"; elapsed=$((elapsed + wait_seconds)); fi",
                ]
            )
            command = self._perf_record_command(posixpath.join(perf_dir, sample.data_file), sample.duration_seconds)
            lines.append("run_child " + " ".join(shlex.quote(part) for part in command))
            lines.append(f"elapsed=$((elapsed + {sample.duration_seconds}))")
        return "\n".join(lines)

    def _write_remote_perf_samples(self, client: paramiko.SSHClient, perf_dir: str) -> None:
        if not self._sampled_perf_enabled():
            return
        payload = json.dumps(_perf_samples_manifest(self.perf_samples), ensure_ascii=False, indent=2) + "\n"
        command = f"cat > {shlex.quote(posixpath.join(perf_dir, 'samples.json'))} <<'AUTOMAN_PERF_SAMPLES'\n{payload}AUTOMAN_PERF_SAMPLES\n"
        self._run_checked(client, command)

    def _remote_perf_export_commands(self, perf_dir: str) -> list[tuple[str, str]]:
        if not self._sampled_perf_enabled():
            return [
                ("perf.script.txt", f"cd {shlex.quote(perf_dir)} && perf script -i perf.data > perf.script.txt 2> perf.script.txt.stderr.log"),
                ("perf.report.txt", f"cd {shlex.quote(perf_dir)} && perf report --stdio -i perf.data > perf.report.txt 2> perf.report.txt.stderr.log"),
            ]

        data_files = " ".join(shlex.quote(sample.data_file) for sample in self.perf_samples)
        script = f"""set -e
cd {shlex.quote(perf_dir)}
found=
first=
for data in {data_files}; do
  if [ -s "$data" ]; then
    found=1
    if [ -z "$first" ]; then first="$data"; fi
    stem=${{data%.data}}
    perf script -i "$data" > "$stem.script.txt" 2> "$stem.script.txt.stderr.log"
    perf report --stdio -i "$data" > "$stem.report.txt" 2> "$stem.report.txt.stderr.log"
  fi
done
if [ -z "$found" ]; then echo "perf sample data was not created" >&2; exit 1; fi
first_stem=${{first%.data}}
cp -f "$first" perf.data
cp -f "$first_stem.script.txt" perf.script.txt
cp -f "$first_stem.report.txt" perf.report.txt
"""
        return [("perf-samples-export", script)]

    def _background_command(self, command: list[str], phase_dir: str, name: str) -> str:
        stdout = shlex.quote(posixpath.join(phase_dir, f"{name}.log"))
        stderr = shlex.quote(posixpath.join(phase_dir, f"{name}.stderr.log"))
        pidfile = shlex.quote(posixpath.join(phase_dir, f"{name}.pid"))
        rendered = " ".join(shlex.quote(part) for part in command)
        return f"nohup {rendered} > {stdout} 2> {stderr} < /dev/null & echo $! > {pidfile}"

    def _stop_command(self, phase_dir: str, name: str, sig: str) -> str:
        pidfile = shlex.quote(posixpath.join(phase_dir, f"{name}.pid"))
        return (
            f"pid=$(cat {pidfile} 2>/dev/null || true); "
            "if [ -z \"$pid\" ]; then exit 0; fi; "
            f"kill -{sig} \"$pid\" 2>/dev/null || true; "
            "for i in $(seq 1 30); do kill -0 \"$pid\" 2>/dev/null || exit 0; sleep 1; done; "
            "kill -KILL \"$pid\" 2>/dev/null || true"
        )

    def _force_stop_command(self, phase_dir: str, name: str) -> str:
        pidfile = shlex.quote(posixpath.join(phase_dir, f"{name}.pid"))
        return (
            f"pid=$(cat {pidfile} 2>/dev/null || true); "
            "if [ -z \"$pid\" ]; then exit 0; fi; "
            "comm=$(ps -p \"$pid\" -o comm= 2>/dev/null || true); "
            "case \"$comm\" in "
            "vmstat|iostat|mpstat|pidstat|perf|sleep|sh) "
            "kill -TERM \"$pid\" 2>/dev/null || true; "
            "sleep 2; "
            "kill -KILL \"$pid\" 2>/dev/null || true ;; "
            "*) exit 0 ;; "
            "esac"
        )

    def _client(self) -> paramiko.SSHClient:
        last_error: Exception | None = None
        for attempt in range(3):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    timeout=15,
                    banner_timeout=30,
                    auth_timeout=15,
                    look_for_keys=False,
                    allow_agent=False,
                )
                return client
            except (paramiko.SSHException, socket.error, TimeoutError) as exc:
                last_error = exc
                client.close()
                if attempt < 2:
                    time.sleep(2)
        raise CollectorError(f"{self.role}: SSH connect failed to {self.user}@{self.host}:{self.port}: {last_error}")

    def _run_checked(self, client: paramiko.SSHClient, command: str, timeout: int = 120) -> None:
        code, out, err = self._run(client, command, timeout)
        if code != 0:
            raise CollectorError(f"{self.role}: {command}: {err.strip() or out.strip() or code}")

    def _run(self, client: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return stdout.channel.recv_exit_status(), out, err

    def _fetch_tree(self, client: paramiko.SSHClient, remote_dir: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        sftp = client.open_sftp()
        try:
            self._fetch_tree_with_sftp(sftp, remote_dir, local_dir)
        finally:
            sftp.close()

    def _fetch_tree_with_sftp(self, sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        for item in sftp.listdir_attr(remote_dir):
            remote_path = posixpath.join(remote_dir, item.filename)
            local_path = local_dir / item.filename
            if stat.S_ISDIR(item.st_mode):
                self._fetch_tree_with_sftp(sftp, remote_path, local_path)
            else:
                if item.st_size > REMOTE_FETCH_MAX_BYTES:
                    marker = local_path.with_name(f"{local_path.name}.skipped.txt")
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    message = (
                        f"skipped remote collector artifact larger than "
                        f"{REMOTE_FETCH_MAX_BYTES} bytes: {remote_path} ({item.st_size} bytes)\n"
                    )
                    marker.write_text(message, encoding="utf-8")
                    self.command_results.append(
                        {
                            "name": "fetch-skip",
                            "command": f"sftp get {remote_path}",
                            "exit_code": 0,
                            "stdout": message,
                            "stderr": "",
                        }
                    )
                    continue
                sftp.get(remote_path, str(local_path))

    def _write_manifest(self, phase: str, status: str, errors: list[str] | None = None) -> None:
        artifacts = _artifact_index(self.output_dir)
        system_files = [item["relative_path"] for item in artifacts if str(item["relative_path"]).startswith("system/")]
        perf_files = [item["relative_path"] for item in artifacts if str(item["relative_path"]).startswith("perf/")]
        manifest = {
            "phase": phase,
            "role": self.role,
            "host_type": "ssh",
            "host": self.host,
            "remote_dir": self._phase_dir(phase),
            "status": status,
            "started_at": self.started_at,
            "ended_at": _now() if status != "running" else None,
            "include_system": self.include_system,
            "include_perf": self.include_perf,
            "system_interval_seconds": self.system_interval,
            "system_tools": sorted(self.system_tools),
            "perf_frequency": self.perf_frequency,
            "perf_call_graph": self.perf_call_graph,
            "perf_mode": self.perf_mode,
            "perf_samples": _perf_samples_manifest(self.perf_samples) if self._sampled_perf_enabled() else [],
            "commands": self.commands,
            "command_results": self.command_results,
            "errors": errors or [],
            "collectors": {
                "system": {
                    "enabled": self.include_system,
                    "status": _component_status(self.include_system, status, errors),
                    "files": system_files,
                    "errors": errors or [] if self.include_system else [],
                },
                "perf": {
                    "enabled": self.include_perf,
                    "status": _component_status(self.include_perf, status, errors),
                    "files": perf_files,
                    "errors": errors or [] if self.include_perf else [],
                },
            },
            "artifacts": artifacts,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _build_perf_samples(
    *,
    mode: str,
    run_mins: int,
    count: int,
    duration_seconds: int,
    delay_seconds: int | None,
    interval_seconds: int | None,
    delay_ratio: float,
    interval_ratio: float,
) -> list[_PerfSample]:
    if mode != "sampled" or count <= 0 or duration_seconds <= 0:
        return []
    total_seconds = max(0, int(run_mins) * 60)
    first_delay = delay_seconds if delay_seconds is not None else int(total_seconds * delay_ratio)
    interval = interval_seconds if interval_seconds is not None else int(total_seconds * interval_ratio)
    first_delay = max(0, first_delay)
    interval = max(0, interval)
    samples: list[_PerfSample] = []
    for index in range(count):
        sample_delay = first_delay + (interval * index)
        if total_seconds > 0 and sample_delay >= total_seconds:
            break
        sample_duration = duration_seconds
        if total_seconds > 0:
            sample_duration = min(sample_duration, max(1, total_seconds - sample_delay))
        samples.append(
            _PerfSample(
                name=f"perf-{index + 1:03d}",
                delay_seconds=sample_delay,
                duration_seconds=sample_duration,
            )
        )
    if samples:
        return samples
    return [_PerfSample(name="perf-001", delay_seconds=0, duration_seconds=duration_seconds)]


def _perf_samples_manifest(samples: list[_PerfSample]) -> list[dict[str, Any]]:
    return [
        {
            "name": sample.name,
            "delay_seconds": sample.delay_seconds,
            "duration_seconds": sample.duration_seconds,
            "data_file": sample.data_file,
            "script_file": sample.script_file,
            "report_file": sample.report_file,
        }
        for sample in samples
    ]


def _call_graph_args(call_graph: str) -> list[str]:
    mode = call_graph.lower()
    if mode == "none":
        return []
    if mode == "fp":
        return ["-g"]
    return ["--call-graph", mode]


def _artifact_index(base_dir: Path) -> list[dict[str, Any]]:
    if not base_dir.exists():
        return []
    artifacts = []
    for path in sorted(item for item in base_dir.rglob("*") if item.is_file() and item.name != "manifest.json"):
        artifacts.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(base_dir)),
                "size_bytes": path.stat().st_size,
            }
        )
    return artifacts


def _component_status(enabled: bool, status: str, errors: list[str] | None) -> str:
    if not enabled:
        return "disabled"
    if status == "running":
        return "running"
    return "failed" if errors else "success"


def _now() -> str:
    return datetime.now().isoformat()


def _local_host_markers() -> set[str]:
    markers = {"localhost", "127.0.0.1", socket.gethostname()}
    try:
        markers.add(socket.getfqdn())
        markers.add(socket.gethostbyname(socket.gethostname()))
    except socket.error:
        pass
    try:
        result = subprocess.run(["hostname", "-I"], text=True, capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            markers.update(part.strip() for part in result.stdout.split() if part.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {marker for marker in markers if marker}
