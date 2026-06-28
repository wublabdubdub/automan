from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SystemCollectorConfig:
    enabled: bool = True
    interval_seconds: int = 1
    host_roles: list[str] = field(default_factory=lambda: ["database"])
    tools: list[str] = field(default_factory=lambda: ["vmstat", "iostat", "pidstat", "mpstat"])


@dataclass(frozen=True)
class PerfCollectorConfig:
    enabled: bool = True
    phases: list[str] = field(default_factory=lambda: ["runBenchmark.sh"])
    host_roles: list[str] = field(default_factory=lambda: ["database"])
    frequency: int = 99
    call_graph: str = "fp"
    record_scope: str = "system"
    mode: str = "sampled"
    sample_count: int = 3
    sample_duration_seconds: int = 60
    sample_delay_seconds: int | None = None
    sample_interval_seconds: int | None = None
    sample_delay_ratio: float = 0.2
    sample_interval_ratio: float = 0.3


@dataclass(frozen=True)
class CollectorConfig:
    enabled: bool = True
    system: SystemCollectorConfig = field(default_factory=SystemCollectorConfig)
    perf: PerfCollectorConfig = field(default_factory=PerfCollectorConfig)


@dataclass(frozen=True)
class DatabaseProfile:
    id: str
    display_name: str
    database_type: str
    storage_engine: str
    test_mode: str
    ddl_profile: str
    ddl_dir: str
    requires_ddl_confirmation: bool
    mars3_defaults: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionInfo:
    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    remote_workdir: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    postgresql_conf: str | None = None
    restart_command: str | None = None
    gpconfig_command: str = "gpconfig"
    execution_host: str = "172.16.100.143"
    execution_port: int = 22
    execution_user: str = "root"
    execution_password: str = ""
    execution_workdir: str = "/root/automan"

    def redacted(self) -> dict[str, Any]:
        return {
            "execution_host": self.execution_host,
            "execution_port": self.execution_port,
            "execution_user": self.execution_user,
            "execution_password": "***" if self.execution_password else "",
            "execution_workdir": self.execution_workdir,
            "ssh_host": self.ssh_host,
            "ssh_port": self.ssh_port,
            "ssh_user": self.ssh_user,
            "ssh_password": "***",
            "remote_workdir": self.remote_workdir,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_name": self.db_name,
            "db_user": self.db_user,
            "db_password": "***",
            "postgresql_conf": self.postgresql_conf,
            "restart_command": self.restart_command,
            "gpconfig_command": self.gpconfig_command,
        }


@dataclass
class Target:
    profile: DatabaseProfile
    connection: ConnectionInfo
    recommended_params: dict[str, str]
    accepted_params: dict[str, str]
    apply_params: bool
    host_facts: dict[str, str | int]
    mars3_options: dict[str, Any] = field(default_factory=dict)
    manual_parameter_commands: list[str] = field(default_factory=list)
    target_id: str | None = None
    explicit_params: dict[str, str] = field(default_factory=dict)
    manual_parameter_commands_auto_generated: bool = True

    @property
    def id(self) -> str:
        return self.target_id or self.profile.id


@dataclass(frozen=True)
class TpccMatrix:
    warehouses: list[int]
    terminals: list[int]
    load_workers: int
    run_mins: int


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    target_id: str
    warehouse: int
    terminals: int
    load_workers: int
    run_mins: int
    ddl_profile: str
    ddl_dir: str
    properties_path: Path
    work_dir: Path
    benchmark_run_dir: Path
    skip_destroy: bool = False
