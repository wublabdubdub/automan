from __future__ import annotations

import unittest
from pathlib import Path

from automan_core.models import ConnectionInfo, DatabaseProfile, Target
from automan_core.sqlbench import relation_size
from automan_core.ssh import CommandResult


class SqlbenchTest(unittest.TestCase):
    def test_relation_size_uses_partition_tree_for_partitioned_tables(self) -> None:
        sql_seen: list[str] = []

        def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
            sql_seen.append(command[-1])
            return CommandResult("psql", 0, "85 MB|89128960\n", "")

        size = relation_size(_target(), ["iot_vehicle_raw_ct1200"], runner)

        self.assertEqual(size["table_data_size"], "85 MB")
        self.assertEqual(size["table_data_size_bytes"], 89128960)
        self.assertIn("pg_catalog.pg_partition_tree(rels.oid)", sql_seen[0])
        self.assertIn("pg_catalog.pg_total_relation_size(size_oid)", sql_seen[0])


def _target() -> Target:
    return Target(
        DatabaseProfile(
            id="ymatrix_mars3",
            display_name="YMatrix mars3",
            database_type="ymatrix",
            storage_engine="mars3",
            test_mode="master_only",
            ddl_profile="ymatrix_mars3_master_only",
            ddl_dir="",
            requires_ddl_confirmation=False,
        ),
        ConnectionInfo(
            ssh_host="127.0.0.1",
            ssh_port=22,
            ssh_user="root",
            ssh_password="secret",
            remote_workdir="/tmp/automan",
            db_host="127.0.0.1",
            db_port=5432,
            db_name="postgres",
            db_user="postgres",
            db_password="secret",
        ),
        {},
        {},
        False,
        {},
    )


if __name__ == "__main__":
    unittest.main()
