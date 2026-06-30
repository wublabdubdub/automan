from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from automan_core import cli
from automan_core.checks import check_collector_readiness, check_task_readiness
from automan_core.config import write_yaml
from automan_core.models import CollectorConfig, ConnectionInfo, DatabaseProfile, PerfCollectorConfig, SystemCollectorConfig, Target
from automan_core.ssh import CommandResult
from automan_core.task_runner import load_task_definition


class ChecksTest(unittest.TestCase):
    def test_missing_perf_fails_with_remediation_hints(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=False),
            perf=PerfCollectorConfig(enabled=True, host_roles=["execution"], frequency=99),
        )

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(Path(tmp), [], collectors, local_runner=missing_perf_runner)

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results))
        self.assertIn("required command missing: perf", text)
        self.assertIn("required: install perf and run as root", text)
        self.assertIn("check: cat /proc/sys/kernel/perf_event_paranoid", text)
        self.assertIn("example temporary fix: sysctl kernel.perf_event_paranoid=-1", text)

    def test_remote_collector_directory_unwritable_fails_with_clear_error(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=False),
            perf=PerfCollectorConfig(enabled=True, host_roles=["database"], frequency=99),
        )
        target = Target(_profile(), _remote_connection(), {}, {}, False, {})

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(
                Path(tmp),
                [target],
                collectors,
                ssh_runner_factory=lambda target: remote_unwritable_runner,
            )

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results), text)
        self.assertIn("remote collector directory is not writable", text)
        self.assertIn("/readonly/automan/.automan_collectors", text)

    def test_remote_sftp_fetch_unavailable_fails_before_run(self) -> None:
        collectors = CollectorConfig(
            system=SystemCollectorConfig(enabled=True, host_roles=["database"], tools=["vmstat"]),
            perf=PerfCollectorConfig(enabled=False),
        )
        target = Target(_profile(), _remote_connection(), {}, {}, False, {})

        with tempfile.TemporaryDirectory() as tmp:
            results = check_collector_readiness(
                Path(tmp),
                [target],
                collectors,
                ssh_runner_factory=lambda target: remote_ok_runner,
                sftp_fetcher=remote_sftp_failed,
            )

        text = "\n".join(result.text for result in results)
        self.assertTrue(any(result.level == "FAIL" for result in results), text)
        self.assertIn("remote SFTP fetch unavailable", text)
        self.assertIn("SFTP subsystem disabled", text)

    def test_ap_check_fails_when_query_set_is_missing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_conf_file(repo, root, "conf/ap/base.yml")
            _copy_conf_file(repo, root, "conf/ap/targets/ym-mars3.yml")
            _copy_conf_file(repo, root, "configs/database-profiles/ymatrix/mars3-master-only.yaml")
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ap", ["ym-mars3"])
            data["all"]["vars"]["collectors"]["enabled"] = False
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertTrue(any(result.level == "FAIL" for result in results), text)
            self.assertIn("AP query directory not found", text)

    def test_tpch_check_auto_mode_allows_missing_data_when_generator_is_available(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_check_inventory(repo, root)
            (root / "tools/tpch-dbgen").mkdir(parents=True)
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertFalse(any(result.level == "FAIL" for result in results), text)
            self.assertTrue(any(result.level == "WARN" and "will be generated" in result.text for result in results), text)

    def test_tpch_check_auto_mode_fails_when_generator_is_missing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_check_inventory(repo, root)
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertTrue(any(result.level == "FAIL" for result in results), text)
            self.assertIn("TPC-H dbgen source not found", text)

    def test_tpch_check_existing_mode_fails_when_load_data_is_missing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_check_inventory(repo, root)
            data = cli._compose_inventory(root, "tpch", ["pg"])
            data["all"]["vars"]["collectors"]["enabled"] = False
            data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
            data["all"]["vars"]["tpch"]["data_prepare"]["mode"] = "existing"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertTrue(any(result.level == "FAIL" for result in results), text)
            self.assertIn("TPC-H data file(s) missing", text)

    def test_tpch_check_fails_when_data_files_are_empty(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_check_inventory(repo, root)
            data_dir = root / "benchmarks/tpch/data/sf1"
            data_dir.mkdir(parents=True)
            for table in ["region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem"]:
                (data_dir / f"{table}.tbl").write_text("", encoding="utf-8")
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertTrue(any(result.level == "FAIL" for result in results), text)
            self.assertIn("empty TPC-H data file(s)", text)

    def test_tpch_ymatrix_backend_check_uses_db_host_and_remote_tools(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_ymatrix_backend_inventory(repo, root)
            task = load_task_definition(root, inventory)
            calls: list[str] = []

            def runner(command: str, timeout: int) -> CommandResult:
                calls.append(command)
                return CommandResult(command, 0, "1\n", "")

            results = check_task_readiness(root, task, local_runner=psql_ok_runner, ssh_runner_factory=lambda target: runner)

            text = "\n".join(result.text for result in results)
            self.assertFalse(any(result.level == "FAIL" for result in results), text)
            self.assertIn("TPC-H YMatrix backend source ready", text)
            self.assertIn("ymatrix_mars3@10.9.8.7", text)
            self.assertTrue(any("command -v psql" in call for call in calls), calls)
            self.assertTrue(any("command -v make" in call for call in calls), calls)
            self.assertTrue(any("command -v gcc" in call for call in calls), calls)
            self.assertTrue(any("command -v ssh" in call for call in calls), calls)
            self.assertTrue(any("command -v scp" in call for call in calls), calls)
            self.assertTrue(any("command -v tar" in call for call in calls), calls)
            self.assertTrue(any("PGHOST='10.9.8.7'" in call and "select 1;" in call for call in calls), calls)
            self.assertTrue(any("gp_segment_configuration" in call for call in calls), calls)

    def test_tpch_ymatrix_backend_check_fails_when_vendor_source_is_missing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = _write_tpch_ymatrix_backend_inventory(repo, root, copy_vendor=False)
            task = load_task_definition(root, inventory)

            results = check_task_readiness(root, task, local_runner=psql_ok_runner, ssh_runner_factory=lambda target: remote_ok_runner)

            text = "\n".join(result.text for result in results)
            self.assertTrue(any(result.level == "FAIL" for result in results), text)
            self.assertIn("TPC-H YMatrix backend source not found or incomplete", text)


def missing_perf_runner(command, **kwargs):
    return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="perf not found")


def psql_ok_runner(command, **kwargs):
    return subprocess.CompletedProcess(args=command, returncode=0, stdout="1\n", stderr="")


def remote_unwritable_runner(command: str, timeout: int) -> CommandResult:
    if ".automan_collectors" in command or "test -w" in command:
        return CommandResult(command, 1, "", "Permission denied")
    return CommandResult(command, 0, "/usr/bin/perf\n", "")


def remote_ok_runner(command: str, timeout: int) -> CommandResult:
    return CommandResult(command, 0, "/usr/bin/tool\n", "")


def remote_sftp_failed(target: Target, remote_path: str, local_path: Path) -> CommandResult:
    return CommandResult(f"sftp get {remote_path}", 255, "", "SFTP subsystem disabled")


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="postgresql_heap_single_node",
        display_name="PostgreSQL heap single node",
        database_type="postgresql",
        storage_engine="heap",
        test_mode="single_node",
        ddl_profile="postgresql_heap_single_node",
        ddl_dir="benchmarks/tpcc/benchmarksql/ddl/postgresql_heap_single_node",
        requires_ddl_confirmation=False,
    )


def _remote_connection() -> ConnectionInfo:
    return ConnectionInfo(
        ssh_host="db.example.test",
        ssh_port=22,
        ssh_user="root",
        ssh_password="secret",
        remote_workdir="/readonly/automan",
        db_host="10.0.0.20",
        db_port=5432,
        db_name="postgres",
        db_user="postgres",
        db_password="secret",
        execution_host="bench.example.test",
    )


def _copy_conf_file(src_root: Path, dst_root: Path, relative: str) -> None:
    _copy_file(src_root / relative, dst_root / relative)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _write_tpch_check_inventory(repo: Path, root: Path) -> Path:
    _copy_conf_file(repo, root, "conf/tpch/base.yml")
    _copy_conf_file(repo, root, "conf/tpch/targets/pg.yml")
    _copy_conf_file(repo, root, "configs/database-profiles/postgresql/heap-single-node.yaml")
    for source in (repo / "benchmarks/tpch/schema/pg").glob("*.sql"):
        _copy_file(source, root / source.relative_to(repo))
    for source in (repo / "benchmarks/tpch/queries/standard").glob("*.sql"):
        _copy_file(source, root / source.relative_to(repo))
    inventory = root / "automan.yml"
    data = cli._compose_inventory(root, "tpch", ["pg"])
    data["all"]["vars"]["collectors"]["enabled"] = False
    data["all"]["vars"]["tpch"]["backend"]["type"] = "internal"
    write_yaml(inventory, data)
    return inventory


def _write_tpch_ymatrix_backend_inventory(repo: Path, root: Path, copy_vendor: bool = True) -> Path:
    _copy_conf_file(repo, root, "conf/tpch/base.yml")
    _copy_conf_file(repo, root, "conf/tpch/targets/ym-mars3.yml")
    _copy_conf_file(repo, root, "configs/database-profiles/ymatrix/mars3-master-only.yaml")
    if copy_vendor:
        for source in [
            repo / "tools/ymatrix-tpch/tpch.sh",
            repo / "tools/ymatrix-tpch/rollout.sh",
        ]:
            _copy_file(source, root / source.relative_to(repo))
    inventory = root / "automan.yml"
    data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
    data["all"]["vars"]["collectors"]["enabled"] = False
    data["all"]["children"]["ymatrix_mars3"]["vars"]["db_host"] = "10.9.8.7"
    data["all"]["children"]["ymatrix_mars3"]["vars"]["config_host"] = "10.1.1.1"
    write_yaml(inventory, data)
    return inventory


if __name__ == "__main__":
    unittest.main()


