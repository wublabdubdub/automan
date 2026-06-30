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


@dataclass(frozen=True)
class KafkaConfig:
    host: str
    port: int
    user: str
    password: str
    kafka_bin: str
    bootstrap_server: str
    topic: str
    partitions: list[int]
    replication_factor: int
    recreate_topic: bool = True


@dataclass(frozen=True)
class MxgateConfig:
    user: str
    password: str
    workdir: str
    binary: str
    source: str
    transform: str
    writer: str
    delimiter: str
    format: str
    time_format: str
    stream_use_gzip: str
    interval_ms: int = 50
    bytes_limit: int = 67108864
    stream_prepared: int = 36
    write_buffer_size: int = 1048576
    compress_pool_size: int = 4096
    max_seg_conn: int = 128
    seg_conn_timeout_millis: int = 30000
    insert_timeout: int = 0
    enable_event_trigger: bool = False


@dataclass(frozen=True)
class TsWriteConfig:
    table: str
    workers: list[int]
    rate_per_worker: list[int]
    vins: list[int]
    vin_interval: list[str]
    duration_seconds: list[int]
    pressure_level: str
    pressure_profiles: dict[str, dict[str, object]]
    producer_fast_mode: bool
    producer_lead_seconds: int
    monitor_interval_seconds: int
    lag_drain_timeout_seconds: int
    require_zero_lag: bool


@dataclass(frozen=True)
class TsQueryConfig:
    dependency_mode: str
    rounds: list[int]
    warmup_rounds: list[int]
    timeout_seconds: int


@dataclass(frozen=True)
class PointQueryConfig:
    dependency_mode: str
    rounds: list[int]
    warmup_rounds: list[int]
    sample_size: list[int]
    timeout_seconds: int


@dataclass(frozen=True)
class TsConfig:
    stages: list[str]
    compress_threshold: list[int]
    kafka: KafkaConfig
    mxgate: MxgateConfig
    write: TsWriteConfig
    query: TsQueryConfig
    point_query: PointQueryConfig


@dataclass(frozen=True)
class TsRunSpec:
    run_id: str
    target_id: str
    stage: str
    compress_threshold: int
    target_table: str
    kafka_topic: str
    work_dir: Path
    run_dir: Path
    benchmark_dir: Path
    database_dir: Path
    logs_dir: Path
    collector_dir: Path
    run_mins: int = 1


@dataclass(frozen=True)
class ApQueryConfig:
    source_table: str
    rounds: list[int]
    warmup_rounds: list[int]
    timeout_seconds: int
    query_set: str


@dataclass(frozen=True)
class ApConfig:
    stages: list[str]
    compress_threshold: list[int]
    query: ApQueryConfig


@dataclass(frozen=True)
class ApRunSpec:
    run_id: str
    target_id: str
    stage: str
    compress_threshold: int
    source_table: str
    query_set: str
    rounds: int
    warmup_rounds: int
    run_dir: Path
    benchmark_dir: Path
    database_dir: Path
    logs_dir: Path
    collector_dir: Path
    run_mins: int = 1


@dataclass(frozen=True)
class TpchDataPrepareConfig:
    mode: str = "auto"
    generator: str = "dbgen"
    source_dir: str = "tools/tpch-dbgen"
    build_command: str = "make"
    dbgen_command: str = "./dbgen"
    force: bool = False


@dataclass(frozen=True)
class TpchBackendConfig:
    type: str = "ymatrix-tpch"
    source_dir: str = "tools/ymatrix-tpch"
    remote_dir: str = "runs/{run_id}/ymatrix-tpch"
    database_type: str = "matrixdb"
    access_method: str = "mars3"
    load_data_type: str = "mxgate"
    optimizer: str = "off"
    preheating_data: bool = True
    explain_analyze: bool = False
    greenplum_path: str = ""
    session_gucs: str = "set statement_mem to '1GB';"


@dataclass(frozen=True)
class TpchConfig:
    stages: list[str]
    compress_threshold: list[int]
    scale_factors: list[int]
    query_streams: list[int]
    run_mins: list[int]
    query_set: str
    data_dir: str
    schema_dir: str
    query_dir: str
    data_prepare: TpchDataPrepareConfig
    backend: TpchBackendConfig


@dataclass(frozen=True)
class TpchRunSpec:
    run_id: str
    target_id: str
    stage: str
    ddl_profile: str
    compress_threshold: int | None
    scale_factor: int
    query_streams: int
    run_mins: int
    run_dir: Path
    benchmark_dir: Path
    database_dir: Path
    logs_dir: Path
    collector_dir: Path
