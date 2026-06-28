from __future__ import annotations

import json
import posixpath
import shlex
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
            perf_command = [
                "perf",
                "record",
                "-F",
                str(self.perf_frequency),
                "-a",
                "-o",
                str(self.perf_dir / "perf.data"),
            ]
            perf_command.extend(_call_graph_args(self.perf_call_graph))
            perf_command.extend(["--", "sleep", "86400"])
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
            for name, command, _, output_dir in self._commands(phase_dir):
                self.commands.append({"name": name, "command": command, "remote_output_dir": output_dir})
                self._run_checked(client, f"command -v {shlex.quote(command[0])} >/dev/null 2>&1")
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
                for command in (
                    f"cd {shlex.quote(perf_dir)} && perf script -i perf.data > perf.script.txt 2> perf.script.txt.stderr.log",
                    f"cd {shlex.quote(perf_dir)} && perf report --stdio -i perf.data > perf.report.txt 2> perf.report.txt.stderr.log",
                ):
                    code, out, err = self._run(client, command, timeout=300)
                    self.command_results.append({"name": command.split()[0], "command": command, "exit_code": code, "stdout": out, "stderr": err})
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
            perf_command = [
                "perf",
                "record",
                "-F",
                str(self.perf_frequency),
                "-a",
                "-o",
                posixpath.join(perf_dir, "perf.data"),
            ]
            perf_command.extend(_call_graph_args(self.perf_call_graph))
            perf_command.extend(["--", "sleep", "86400"])
            commands.append(
                (
                    "perf-record",
                    perf_command,
                    "INT",
                    perf_dir,
                )
            )
        return commands

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
            "vmstat|iostat|mpstat|pidstat|perf|sleep) "
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
