from __future__ import annotations

import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import load_yaml
from automan_core.task_runner import run_task_campaign


class TaskRunnerTest(unittest.TestCase):
    def test_run_task_plan_only_generates_manual_parameter_commands(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            task = root / "task.yaml"
            task.write_text(
                """
benchmark: tpcc
execution:
  host: 172.16.100.143
  workdir: /root/automan
matrix:
  warehouses: [100]
  terminals: [100, 500]
  load_workers: 32
  run_mins: 15
targets:
  - id: pg12_tpcc
    profile: postgresql_heap_single_node
    connection:
      config_ssh:
        host: 192.168.100.29
        user: 12pg
      database:
        host: 192.168.100.29
        port: 5232
        name: postgres
        user: zhangchen
        password: secret
      postgresql_conf: /home/12pg/data/postgresql.conf
      restart_command: pg_ctl restart -D /home/12pg/data
    database_parameters:
      max_connections: "550"
      shared_buffers: 62GB
""",
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                campaign_dir = run_task_campaign(root, task, plan_only=True)

            plan = load_yaml(campaign_dir / "resolved-plan.yaml")
            self.assertEqual(plan["targets"][0]["id"], "pg12_tpcc")
            self.assertFalse(plan["targets"][0]["apply_params"])
            self.assertEqual(plan["targets"][0]["parameter_application"], "manual_only")
            commands = (campaign_dir / "manual-parameter-commands.sh").read_text(encoding="utf-8")
            self.assertIn("postgresql.conf", commands)
            self.assertIn("max_connections = '550'", commands)
            self.assertIn("pg_ctl restart -D /home/12pg/data", commands)
            self.assertEqual(len(plan["runs"]), 2)


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
