from __future__ import annotations

import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path

from automan_core.config import load_yaml
import automan_core.task_runner as task_runner
from automan_core.task_runner import CampaignFailedError
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

    def test_run_task_campaign_raises_when_execution_marks_campaign_failed(self) -> None:
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
  warehouses: [10]
  terminals: [20]
  load_workers: 8
  run_mins: 1
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
""",
                encoding="utf-8",
            )
            old_execute = task_runner.execute_campaign
            try:
                def fake_execute(root, campaign_id, targets, runs, collectors):
                    campaign_dir = root / "runs" / "campaigns" / campaign_id
                    load_yaml(campaign_dir / "status.json")
                    from automan_core.config import write_json
                    write_json(campaign_dir / "status.json", {"campaign_id": campaign_id, "status": "failed"})
                    progress = load_yaml(campaign_dir / "progress.json")
                    progress["status"] = "failed"
                    progress["last_error"] = "runDatabaseBuild.sh: ERROR: Must create extension matrixts before using mars3"
                    write_json(campaign_dir / "progress.json", progress)

                task_runner.execute_campaign = fake_execute
                with self.assertRaises(CampaignFailedError) as caught:
                    with redirect_stdout(io.StringIO()):
                        run_task_campaign(root, task, plan_only=False)
            finally:
                task_runner.execute_campaign = old_execute

            self.assertIn("matrixts", str(caught.exception))


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
