from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automan_core.collectors import CollectorManager, _LocalHostCollector, _RemoteHostCollector
from automan_core.models import ConnectionInfo, DatabaseProfile, RunSpec, Target


class CollectorsTest(unittest.TestCase):
    def test_local_system_collector_writes_manifest_and_system_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run/collectors/runBenchmark.sh/database"
            collector = _LocalHostCollector(
                "database",
                output_dir,
                include_system=True,
                include_perf=False,
                system_interval=1,
                system_tools={"vmstat"},
                perf_frequency=99,
                perf_call_graph="fp",
            )

            with patch("automan_core.collectors.subprocess.Popen", side_effect=fake_popen):
                collector.start("runBenchmark.sh")
                collector.stop("runBenchmark.sh")

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["phase"], "runBenchmark.sh")
            self.assertEqual(manifest["role"], "database")
            self.assertEqual(manifest["status"], "success")
            self.assertTrue(manifest["include_system"])
            self.assertFalse(manifest["include_perf"])
            self.assertEqual(manifest["system_tools"], ["vmstat"])
            self.assertEqual(manifest["collectors"]["system"]["status"], "success")
            self.assertEqual(manifest["collectors"]["perf"]["status"], "disabled")
            artifact_paths = [item["path"].replace("\\", "/") for item in manifest["artifacts"]]
            self.assertTrue(any(path.endswith("system/vmstat.log") for path in artifact_paths))
            self.assertTrue((output_dir / "system" / "vmstat.log").exists())

    def test_local_collector_manager_creates_system_perf_dirs_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = _run(root)
            manager = CollectorManager(
                root,
                _target(),
                run,
                config={
                    "enabled": True,
                    "system": {"enabled": True, "host_roles": ["database"], "tools": ["vmstat"]},
                    "perf": {"enabled": True, "host_roles": ["database"], "phases": ["runBenchmark.sh"], "frequency": 99},
                },
            )

            with patch("automan_core.collectors.subprocess.Popen", side_effect=fake_popen):
                with patch("automan_core.collectors.subprocess.run", side_effect=fake_run):
                    manager.start_phase("runBenchmark.sh")
                    manager.stop_phase("runBenchmark.sh")

            output_dir = root / "runs" / run.run_id / "collectors" / "runBenchmark.sh" / "database"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((output_dir / "system").is_dir())
            self.assertTrue((output_dir / "perf").is_dir())
            self.assertEqual(manifest["status"], "success")
            self.assertTrue(manifest["include_system"])
            self.assertTrue(manifest["include_perf"])
            artifact_paths = [item["path"].replace("\\", "/") for item in manifest["artifacts"]]
            self.assertTrue(any(path.endswith("system/vmstat.log") for path in artifact_paths))
            self.assertTrue(any(path.endswith("perf/perf.data") for path in artifact_paths))

    def test_collector_manager_respects_perf_phase_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CollectorManager(
                root,
                _target(),
                _run(root),
                config={
                    "enabled": True,
                    "system": {"enabled": True, "host_roles": ["database"], "tools": ["vmstat"]},
                    "perf": {"enabled": True, "host_roles": ["database"], "phases": ["runBenchmark.sh"], "frequency": 99},
                },
            )

            build_collectors = manager._collectors_for_phase("runDatabaseBuild.sh")
            benchmark_collectors = manager._collectors_for_phase("runBenchmark.sh")
            destroy_collectors = manager._collectors_for_phase("runDatabaseDestroy.sh")

            self.assertEqual(len(build_collectors), 1)
            self.assertTrue(build_collectors[0].include_system)
            self.assertFalse(build_collectors[0].include_perf)
            self.assertEqual(len(benchmark_collectors), 1)
            self.assertTrue(benchmark_collectors[0].include_system)
            self.assertTrue(benchmark_collectors[0].include_perf)
            self.assertEqual(destroy_collectors, [])

    def test_remote_collector_skips_oversized_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "collectors"
            collector = _RemoteHostCollector(
                "database",
                output_dir,
                host="db.example",
                port=22,
                user="mxadmin",
                password="secret",
                remote_base_dir="/home/mxadmin/automan",
                run_id="run1",
                include_system=True,
                include_perf=True,
                system_interval=1,
                system_tools={"vmstat"},
                perf_frequency=99,
                perf_call_graph="fp",
            )

            collector._fetch_tree_with_sftp(
                FakeSFTP({"perf.script.txt": 129 * 1024 * 1024}),
                "/remote/collectors",
                output_dir,
            )

            marker = output_dir / "perf.script.txt.skipped.txt"
            self.assertTrue(marker.exists())
            self.assertIn("skipped remote collector artifact larger than", marker.read_text(encoding="utf-8"))
            self.assertEqual(collector.command_results[0]["name"], "fetch-skip")

    def test_stop_phase_attempts_cleanup_after_collector_stop_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CollectorManager(root, _target(), _run(root), config={"enabled": False})
            events: list[tuple[str, str]] = []

            class FailingCollector:
                def stop(self, phase):
                    events.append(("stop", phase))
                    raise RuntimeError("No existing session")

                def cleanup(self, phase):
                    events.append(("cleanup", phase))

            manager._active["runDatabaseBuild.sh"] = [FailingCollector()]

            with self.assertRaisesRegex(Exception, "No existing session"):
                manager.stop_phase("runDatabaseBuild.sh")

            self.assertEqual(events, [("stop", "runDatabaseBuild.sh"), ("cleanup", "runDatabaseBuild.sh")])


class FakeProcess:
    def poll(self):
        return None

    def send_signal(self, signal):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def fake_popen(command, stdout, stderr, text, cwd):
    stdout.write("sample\n")
    stderr.write("")
    stdout.flush()
    stderr.flush()
    if command and command[0] == "perf":
        perf_data = Path(command[command.index("-o") + 1])
        perf_data.parent.mkdir(parents=True, exist_ok=True)
        perf_data.write_bytes(b"PERF")
    return FakeProcess()


class FakeSFTPAttr:
    def __init__(self, filename: str, size: int) -> None:
        self.filename = filename
        self.st_size = size
        self.st_mode = 0


class FakeSFTP:
    def __init__(self, files: dict[str, int]) -> None:
        self.files = files

    def listdir_attr(self, remote_dir):
        return [FakeSFTPAttr(name, size) for name, size in self.files.items()]

    def get(self, remote_path, local_path):
        Path(local_path).write_text("downloaded", encoding="utf-8")


def fake_run(command, **kwargs):
    return subprocess.CompletedProcess(args=command, returncode=0, stdout="127.0.0.1\n", stderr="")


def _target() -> Target:
    return Target(_profile(), _connection(), {}, {}, False, {})


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


def _connection() -> ConnectionInfo:
    return ConnectionInfo(
        ssh_host="localhost",
        ssh_port=22,
        ssh_user="root",
        ssh_password="secret",
        remote_workdir="/root/automan",
        db_host="localhost",
        db_port=5432,
        db_name="postgres",
        db_user="postgres",
        db_password="secret",
        execution_host="localhost",
    )


def _run(root: Path) -> RunSpec:
    return RunSpec(
        run_id="run1",
        target_id="postgresql_heap_single_node",
        warehouse=100,
        terminals=100,
        load_workers=8,
        run_mins=60,
        ddl_profile="postgresql_heap_single_node",
        ddl_dir="benchmarks/tpcc/benchmarksql/ddl/postgresql_heap_single_node",
        properties_path=root / "work/run1/tpcc.properties",
        work_dir=root / "work/run1",
        benchmark_run_dir=root / "work/run1/benchmarksql/run",
    )


if __name__ == "__main__":
    unittest.main()
