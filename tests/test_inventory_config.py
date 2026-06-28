from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core import cli
from automan_core.config import load_yaml, write_yaml
from automan_core.task_runner import load_task_definition, run_task_job, validate_task_definition


class InventoryConfigTest(unittest.TestCase):
    def test_pg_inventory_loads_target_matrix_passwords_and_manual_commands(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "automan.yml"
            write_yaml(inventory, cli._compose_tpcc_inventory(repo, ["pg"]))
            task = load_task_definition(repo, inventory)

        self.assertEqual(task.matrix.warehouses, [100])
        self.assertEqual(task.matrix.terminals, [100, 500])
        self.assertEqual(task.matrix.load_workers, 32)
        self.assertEqual(task.matrix.run_mins, 15)
        self.assertEqual(len(task.targets), 1)
        self.assertEqual(task.collectors.system.interval_seconds, 1)
        self.assertEqual(task.collectors.perf.frequency, 99)
        self.assertEqual(task.collectors.perf.phases, ["runBenchmark.sh"])
        self.assertEqual(task.collectors.perf.mode, "sampled")
        self.assertEqual(task.collectors.perf.sample_count, 3)
        self.assertEqual(task.collectors.perf.sample_duration_seconds, 60)

        target = task.targets[0]
        self.assertEqual(target.id, "pg")
        self.assertEqual(target.profile.id, "postgresql_heap_single_node")
        self.assertEqual(target.connection.execution_host, "172.16.100.143")
        self.assertEqual(target.connection.execution_workdir, "/root/automan")
        self.assertEqual(target.connection.db_password, "")
        self.assertEqual(target.connection.ssh_password, "")
        self.assertEqual(target.connection.ssh_host, target.connection.db_host)
        self.assertFalse(target.apply_params)
        self.assertTrue(any("ALTER SYSTEM SET max_connections = '550';" in line for line in target.manual_parameter_commands))

        messages = validate_task_definition(task)
        self.assertTrue(any(message.level == "OK" and "1 benchmark target" in message.text for message in messages))

    def test_ymatrix_inventory_auto_fills_tpcc_memory_parameters(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "automan.yml"
            write_yaml(inventory, cli._compose_tpcc_inventory(repo, ["ym-mars3"]))
            task = load_task_definition(repo, inventory)

        target = task.targets[0]
        commands = "\n".join(target.manual_parameter_commands)

        self.assertEqual(target.profile.id, "ymatrix_mars3_master_only")
        self.assertEqual(target.accepted_params["max_connections"], "1050")
        self.assertIn("gpconfig -c max_connections -v 1050", commands)
        self.assertIn("gpconfig -c shared_buffers -v 16GB", commands)
        self.assertIn("gpconfig -c effective_cache_size -v 44GB", commands)
        self.assertIn("gpconfig -c work_mem -v 29MB", commands)
        self.assertIn("gpconfig -c maintenance_work_mem -v 3GB", commands)
        self.assertIn("gpconfig -c checkpoint_completion_target -v 0.9", commands)
        self.assertIn("gpconfig -c max_wal_size -v 64GB", commands)
        self.assertIn("gpconfig -c min_wal_size -v 8GB", commands)
        self.assertIn("gpconfig -c vacuum_cost_limit -v 10000", commands)

    def test_ymatrix_inventory_recommendations_change_with_host_facts(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            _copy_minimal_repo_files(
                repo,
                root,
                [
                    "configs/database-profiles/ymatrix/mars3-master-only.yaml",
                    "benchmarks/tpcc/benchmarksql/ddl/ymatrix_mars3_master_only/profile.yaml",
                ],
            )
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  vars:
    benchmark: tpcc
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
    ymatrix_mars3:
      vars:
        db_type: ymatrix
        storage_engine: mars3
        test_mode: master_only
        tpcc_warehouses: [100]
        tpcc_terminals: [100]
        tpcc_load_workers: 16
        tpcc_run_mins: 15
        db_host: 172.16.100.62
        db_port: 5432
        db_name: tpcc
        db_user: zhangchen
        config_user: mxadmin
        host_facts:
          cpu_threads: 128
          memory_gb: 96
        database_parameters: {}
""",
                encoding="utf-8",
            )

            task = load_task_definition(root, inventory)

        params = task.targets[0].accepted_params
        self.assertEqual(params["max_connections"], "1024")
        self.assertEqual(params["shared_buffers"], "24GB")
        self.assertEqual(params["effective_cache_size"], "67GB")
        self.assertEqual(params["work_mem"], "24MB")
        self.assertEqual(params["maintenance_work_mem"], "4GB")

    def test_inventory_plan_only_records_manual_parameter_commands(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            inventory = root / "automan.yml"
            inventory.write_text(
                """
all:
  children:
    bench:
      hosts:
        bench01:
          ansible_host: 172.16.100.143
          ansible_user: root
          automan_workdir: /root/automan
    pg:
      vars:
        db_type: postgresql
        storage_engine: heap
        test_mode: single_node
        tpcc_warehouses: [100]
        tpcc_terminals: [100, 500]
        tpcc_load_workers: 32
        tpcc_run_mins: 15
        db_host: 192.168.100.29
        db_port: 5232
        db_name: postgres
        db_user: zhangchen
        db_password: secret
        config_user: 12pg
        config_workdir: /home/12pg/automan
        database_parameters:
          max_connections: "550"
          shared_buffers: 62GB
""",
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                job_dir = run_task_job(root, inventory, plan_only=True)

            plan = load_yaml(job_dir / "resolved-plan.yaml")
            self.assertEqual(plan["targets"][0]["id"], "pg")
            self.assertEqual(len(plan["runs"]), 2)
            self.assertEqual(plan["targets"][0]["connection"]["db_password"], "***")
            self.assertEqual(plan["targets"][0]["connection"]["ssh_host"], "192.168.100.29")
            commands = "\n".join(plan["targets"][0]["manual_parameter_commands"])
            self.assertIn("ALTER SYSTEM SET max_connections = '550';", commands)
            self.assertNotIn("pg_ctl restart", commands)
            self.assertFalse((job_dir / "manual-parameter-commands.sh").exists())


def _copy_minimal_repo_files(src: Path, dst: Path, extra_files: list[str] | None = None) -> None:
    for relative in [
        "configs/database-profiles/postgresql/heap-single-node.yaml",
        "benchmarks/tpcc/benchmarksql/props.template",
        *(extra_files or []),
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
