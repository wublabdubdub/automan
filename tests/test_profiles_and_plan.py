from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automan_core.models import ConnectionInfo, Target, TpccMatrix
from automan_core.plan import build_run_specs, write_campaign_files
from automan_core.profiles import load_database_profiles, load_database_types


class ProfileAndPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_database_type_and_profile_loading(self) -> None:
        db_types = load_database_types(self.root)
        profiles = load_database_profiles(self.root)

        self.assertIn("postgresql", db_types)
        self.assertIn("ymatrix", db_types)
        self.assertIn("postgresql_heap_single_node", profiles)
        self.assertIn("ymatrix_heap_master_only", profiles)
        self.assertIn("ymatrix_mars3_master_only", profiles)
        self.assertEqual(profiles["ymatrix_mars3_master_only"].mars3_defaults["compress_threshold"], 1200)

    def test_run_matrix_expands_targets_warehouses_and_terminals(self) -> None:
        profiles = load_database_profiles(self.root)
        conn = ConnectionInfo(
            ssh_host="127.0.0.1",
            ssh_port=22,
            ssh_user="root",
            ssh_password="secret",
            remote_workdir="/root/automan",
            db_host="127.0.0.1",
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_password="secret",
        )
        targets = [
            Target(profiles["postgresql_heap_single_node"], conn, {}, {}, False, {"cpu_threads": 16, "memory_gb": 64}),
            Target(profiles["ymatrix_heap_master_only"], conn, {}, {}, False, {"cpu_threads": 16, "memory_gb": 64}),
            Target(profiles["ymatrix_mars3_master_only"], conn, {}, {}, False, {"cpu_threads": 16, "memory_gb": 64}),
        ]
        matrix = TpccMatrix(warehouses=[100, 1000], terminals=[100, 500, 1000], load_workers=8, run_mins=60)

        runs = build_run_specs(self.root, "campaign", targets, matrix)

        self.assertEqual(len(runs), 18)
        self.assertIn("campaign-ymatrix_mars3_master_only-w1000-c1000", {run.run_id for run in runs})
        first_by_target = {}
        for run in runs:
            first_by_target.setdefault(run.target_id, run)
        self.assertTrue(all(run.skip_destroy for run in first_by_target.values()))
        self.assertEqual(sum(1 for run in runs if run.skip_destroy), len(targets))

    def test_write_campaign_files_redacts_passwords(self) -> None:
        profiles = load_database_profiles(self.root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "benchmarks/tpcc/benchmarksql").mkdir(parents=True)
            (root / "benchmarks/tpcc/benchmarksql/props.template").write_text(
                "conn=${jdbc_url}\nuser=${user}\npassword=${password}\nwarehouses=${warehouses}\nloadWorkers=${load_workers}\nterminals=${terminals}\nrunMins=${run_minutes}\n",
                encoding="utf-8",
            )
            conn = ConnectionInfo(
                ssh_host="127.0.0.1",
                ssh_port=22,
                ssh_user="root",
                ssh_password="ssh-secret",
                remote_workdir="/root/automan",
                db_host="127.0.0.1",
                db_port=5432,
                db_name="postgres",
                db_user="postgres",
                db_password="db-secret",
            )
            target = Target(profiles["postgresql_heap_single_node"], conn, {}, {}, False, {"cpu_threads": 16, "memory_gb": 64})
            matrix = TpccMatrix(warehouses=[100], terminals=[100], load_workers=8, run_mins=60)
            runs = build_run_specs(root, "campaign", [target], matrix)

            campaign_dir = write_campaign_files(root, "campaign", [target], matrix, runs, {})

            plan = (campaign_dir / "resolved-plan.yaml").read_text(encoding="utf-8")
            self.assertIn("ssh_password: '***'", plan)
            self.assertIn("db_password: '***'", plan)
            self.assertIn("destroy_policy: schema_probe_then_destroy_if_needed", plan)
            self.assertIn("skip_destroy: true", plan)
            self.assertIn("parameter_application: manual_only", plan)
            progress = (campaign_dir / "progress.json").read_text(encoding="utf-8")
            self.assertIn('"execution_host": "172.16.100.143"', progress)
            self.assertIn('"config_host": "127.0.0.1"', progress)
            self.assertIn('"database_host": "127.0.0.1"', progress)
            properties = runs[0].properties_path.read_text(encoding="utf-8")
            self.assertIn("password=db-secret", properties)


if __name__ == "__main__":
    unittest.main()
