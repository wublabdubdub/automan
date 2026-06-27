from __future__ import annotations

import re

from automan_core.models import DatabaseProfile
from automan_core.ssh import SSHClient


def probe_host(ssh: SSHClient) -> dict[str, str | int]:
    command = "printf 'CPU_THREADS='; nproc; printf 'MEM_KB='; awk '/MemTotal/ {print $2}' /proc/meminfo; uname -a"
    result = ssh.run(command, timeout=30)
    facts: dict[str, str | int] = {
        "probe_exit_code": result.exit_code,
        "probe_stdout": result.stdout,
        "probe_stderr": result.stderr,
    }
    cpu_match = re.search(r"CPU_THREADS=(\d+)", result.stdout)
    mem_match = re.search(r"MEM_KB=(\d+)", result.stdout)
    if cpu_match:
        facts["cpu_threads"] = int(cpu_match.group(1))
    if mem_match:
        facts["memory_gb"] = max(1, int(int(mem_match.group(1)) / 1024 / 1024))
    return facts


def recommend_params(profile: DatabaseProfile, facts: dict[str, str | int], max_terminals: int) -> dict[str, str]:
    cpu_threads = int(facts.get("cpu_threads", 16))
    memory_gb = int(facts.get("memory_gb", 64))
    max_connections = max(max_terminals + 50, cpu_threads * 8)

    if profile.database_type == "postgresql":
        shared_buffers = max(1, int(memory_gb * 0.25))
        effective_cache_size = max(1, int(memory_gb * 0.7))
        work_mem_mb = max(4, min(256, int(memory_gb * 1024 / max(max_connections, 1) / 4)))
        maintenance_work_mem_gb = max(1, min(16, int(memory_gb * 0.05)))
        return {
            "max_connections": str(max_connections),
            "shared_buffers": f"{shared_buffers}GB",
            "effective_cache_size": f"{effective_cache_size}GB",
            "work_mem": f"{work_mem_mb}MB",
            "maintenance_work_mem": f"{maintenance_work_mem_gb}GB",
            "checkpoint_timeout": "30min",
            "max_wal_size": "64GB",
        }

    ymatrix_max_connections = max(max_terminals + 50, 128)
    return {
        "max_connections": str(ymatrix_max_connections),
        "shared_buffers": f"{_ceil_to_multiple(max(1, int(memory_gb * 0.25)), 8)}GB",
        "effective_cache_size": f"{_ceil_to_multiple(max(1, int(memory_gb * 0.50)), 8)}GB",
        "work_mem": "4MB",
        "maintenance_work_mem": "2GB",
        "checkpoint_completion_target": "0.9",
        "max_wal_size": "64GB",
        "min_wal_size": "8GB",
        "vacuum_cost_limit": "10000",
    }


def recommended_load_workers(facts: dict[str, str | int]) -> int:
    cpu_threads = int(facts.get("cpu_threads", 16))
    return max(4, min(64, int(cpu_threads / 2)))


def _ceil_to_multiple(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple
