from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from automan_core.config import write_json, write_yaml
from automan_core.models import RunSpec, Target, TpccMatrix


def new_campaign_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-tpcc")


def build_run_specs(root: Path, campaign_id: str, targets: list[Target], matrix: TpccMatrix) -> list[RunSpec]:
    specs: list[RunSpec] = []
    for target in targets:
        for warehouse in matrix.warehouses:
            for terminals in matrix.terminals:
                run_id = f"{campaign_id}-{target.id}-w{warehouse}-c{terminals}"
                work_dir = root / "work" / "tpcc" / "benchmarksql" / run_id
                properties_path = work_dir / "tpcc.properties"
                specs.append(
                    RunSpec(
                        run_id=run_id,
                        target_id=target.id,
                        warehouse=warehouse,
                        terminals=terminals,
                        load_workers=matrix.load_workers,
                        run_mins=matrix.run_mins,
                        ddl_profile=target.profile.ddl_profile,
                        ddl_dir=target.profile.ddl_dir,
                        properties_path=properties_path,
                        work_dir=work_dir,
                        benchmark_run_dir=work_dir / "benchmarksql" / "run",
                    )
                )
    return specs


def render_properties(root: Path, target: Target, run: RunSpec) -> str:
    template_path = root / "benchmarks" / "tpcc" / "benchmarksql" / "props.template"
    text = template_path.read_text(encoding="utf-8")
    jdbc_url = f"jdbc:postgresql://{target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}"
    values = {
        "jdbc_url": jdbc_url,
        "user": target.connection.db_user,
        "password": target.connection.db_password,
        "warehouses": str(run.warehouse),
        "load_workers": str(run.load_workers),
        "terminals": str(run.terminals),
        "run_minutes": str(run.run_mins),
        "limit_txns_per_min": "10000000",
        "result_directory": f"{root / 'runs' / run.run_id / 'benchmark' / 'result'}",
    }
    return Template(text).safe_substitute(values)


def write_campaign_files(
    root: Path,
    campaign_id: str,
    targets: list[Target],
    matrix: TpccMatrix,
    runs: list[RunSpec],
    mars3_options: dict[str, Any],
) -> Path:
    campaign_dir = root / "runs" / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)

    target_map = {target.id: target for target in targets}
    plan = {
        "campaign_id": campaign_id,
        "benchmark": "tpcc",
        "targets": [
            {
                "id": target.id,
                "display_name": target.profile.display_name,
                "database_type": target.profile.database_type,
                "storage_engine": target.profile.storage_engine,
                "test_mode": target.profile.test_mode,
                "connection": target.connection.redacted(),
                "host_facts": target.host_facts,
                "recommended_params": target.recommended_params,
                "accepted_params": target.accepted_params,
                "apply_params": target.apply_params,
                "mars3_options": target.mars3_options,
                "ddl_profile": target.profile.ddl_profile,
                "ddl_dir": target.profile.ddl_dir,
            }
            for target in targets
        ],
        "matrix": {
            "warehouses": matrix.warehouses,
            "terminals": matrix.terminals,
            "load_workers": matrix.load_workers,
            "run_mins": matrix.run_mins,
        },
        "mars3_options": mars3_options,
        "runs": [
            {
                "run_id": run.run_id,
                "target_id": run.target_id,
                "warehouse": run.warehouse,
                "terminals": run.terminals,
                "load_workers": run.load_workers,
                "run_mins": run.run_mins,
                "ddl_profile": run.ddl_profile,
                "ddl_dir": run.ddl_dir,
                "properties_path": str(run.properties_path),
                "work_dir": str(run.work_dir),
                "benchmark_run_dir": str(run.benchmark_run_dir),
            }
            for run in runs
        ],
        "scheduling": {
            "different_hosts": "parallel",
            "same_host": "serial_by_selection_order",
        },
        "per_run_sequence": [
            "runDatabaseDestroy.sh",
            "runDatabaseBuild.sh",
            "runBenchmark.sh",
        ],
    }
    write_yaml(campaign_dir / "campaign.yaml", plan)
    write_yaml(campaign_dir / "resolved-plan.yaml", plan)

    progress = {
        "campaign_id": campaign_id,
        "status": "planned",
        "total_runs": len(runs),
        "finished_runs": 0,
        "success_runs": 0,
        "running_runs": 0,
        "failed_runs": 0,
        "pending_runs": len(runs),
        "targets": [
            {
                "target_id": target.id,
                "host": target.connection.ssh_host,
                "status": "pending",
                "current_run": None,
                "current_phase": None,
                "finished_runs": 0,
                "total_runs": sum(1 for run in runs if run.target_id == target.id),
            }
            for target in targets
        ],
    }
    write_json(campaign_dir / "progress.json", progress)
    write_json(campaign_dir / "status.json", {"campaign_id": campaign_id, "status": "planned"})

    for run in runs:
        target = target_map[run.target_id]
        run_dir = root / "runs" / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run.properties_path.parent.mkdir(parents=True, exist_ok=True)
        run.properties_path.write_text(render_properties(root, target, run), encoding="utf-8")
        write_yaml(
            run_dir / "resolved-task.yaml",
            {
                "run_id": run.run_id,
                "target_id": run.target_id,
                "warehouse": run.warehouse,
                "terminals": run.terminals,
                "load_workers": run.load_workers,
                "run_mins": run.run_mins,
                "ddl_profile": run.ddl_profile,
                "ddl_dir": run.ddl_dir,
                "benchmarksql_properties": str(run.properties_path),
                "work_dir": str(run.work_dir),
                "benchmark_run_dir": str(run.benchmark_run_dir),
                "command_sequence": [
                    "runDatabaseDestroy.sh",
                    "runDatabaseBuild.sh",
                    "runBenchmark.sh",
                ],
            },
        )
        write_json(run_dir / "status.json", {"run_id": run.run_id, "status": "pending", "phase": None})
    return campaign_dir
