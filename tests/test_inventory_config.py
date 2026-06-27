from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import load_yaml
from automan_core.task_runner import load_task_definition, run_task_campaign, validate_task_definition


class InventoryConfigTest(unittest.TestCase):
    def test_pg_inventory_loads_target_matrix_passwords_and_manual_commands(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        task = load_task_definition(repo, repo / "conf" / "tpcc" / "pg.yml")

        self.assertEqual(task.matrix.warehouses, [100])
        self.assertEqual(task.matrix.terminals, [100, 500])
        self.assertEqual(task.matrix.load_workers, 32)
        self.assertEqual(task.matrix.run_mins, 15)
        self.assertEqual(len(task.targets), 1)

        target = task.targets[0]
        self.assertEqual(target.id, "pg")
        self.assertEqual(target.profile.id, "postgresql_heap_single_node")
        self.assertEqual(target.connection.execution_host, "172.16.100.143")
        self.assertEqual(target.connection.execution_workdir, "/root/automan")
        self.assertEqual(target.connection.db_password, "")
        self.assertEqual(target.connection.ssh_password, "")
        self.assertFalse(target.apply_params)
        self.assertTrue(any("postgresql.conf" in line for line in target.manual_parameter_commands))
        self.assertTrue(any("max_connections = '550'" in line for line in target.manual_parameter_commands))

        messages = validate_task_definition(task)
        self.assertTrue(any(message.level == "OK" and "1 benchmark target" in message.text for message in messages))

    def test_inventory_plan_only_generates_manual_parameter_script(self) -> None:
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
        config_host: 192.168.100.29
        config_user: 12pg
        config_workdir: /home/12pg/automan
        postgresql_conf: /home/12pg/data/postgresql.conf
        restart_command: pg_ctl restart -D /home/12pg/data
        database_parameters:
          max_connections: "550"
          shared_buffers: 62GB
""",
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                campaign_dir = run_task_campaign(root, inventory, plan_only=True)

            plan = load_yaml(campaign_dir / "resolved-plan.yaml")
            self.assertEqual(plan["targets"][0]["id"], "pg")
            self.assertEqual(len(plan["runs"]), 2)
            self.assertEqual(plan["targets"][0]["connection"]["db_password"], "***")
            script = (campaign_dir / "manual-parameter-commands.sh").read_text(encoding="utf-8")
            self.assertIn("max_connections = '550'", script)
            self.assertIn("pg_ctl restart -D /home/12pg/data", script)


def _copy_minimal_repo_files(src: Path, dst: Path) -> None:
    for relative in [
        "configs/database-profiles/postgresql/heap-single-node.yaml",
        "benchmarks/tpcc/benchmarksql/props.template",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
