from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automan_core import cli
from automan_core.config import load_yaml, write_json, write_yaml
from automan_core.list_results import completed_result_rows, show_completed_results
from automan_core.models import TsRunSpec
from automan_core.task_runner import load_task_definition, run_task_job, validate_task_definition
from automan_core.ts import (
    _mxgate_log_command,
    _mxgate_status_command,
    _mxgate_stop_command,
    _parse_mxgate_timing_log,
    _producer_start_command,
    _sample_ranges,
    _table_data_size,
    _ts_table_ddl,
    _write_mxgate_conf,
    build_ts_run_specs,
    execute_ts_job,
    kafka_check,
    kafka_topic_commands,
)
from automan_core.ssh import CommandResult


class TsBenchmarkTest(unittest.TestCase):
    def test_configure_ts_ym_mars3_generates_ts_inventory(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_configure_templates(repo, root)
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-t", "ts", "-c", "ym-mars3", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            inventory = load_yaml(root / "automan.yml")
            self.assertEqual(inventory["all"]["vars"]["benchmark"], "ts")
            self.assertEqual(inventory["all"]["vars"]["ts_stages"], ["ts-write", "ts-query", "point-query"])
            self.assertEqual(inventory["all"]["vars"]["compress_threshold"], [1200, 4096, 8192, 32768])
            self.assertEqual(inventory["all"]["vars"]["kafka"]["topic"], "iot_vehicle_raw_test")
            self.assertEqual(inventory["all"]["vars"]["ts_write"]["pressure_level"], "medium")
            self.assertEqual(inventory["all"]["vars"]["ts_write"]["pressure_profiles"]["high"]["target_qps"], 200000)
            self.assertIn("ymatrix_mars3", inventory["all"]["children"])
            self.assertIn("configured targets: ym-mars3", output.getvalue())

    def test_ts_inventory_validates_kafka_topic_and_dependency_modes(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))

            task = load_task_definition(root, inventory)
            messages = validate_task_definition(task)

            self.assertEqual(task.benchmark, "ts")
            self.assertIsNotNone(task.ts_config)
            self.assertTrue(any(message.text == "benchmark: ts" and message.level == "OK" for message in messages))
            self.assertTrue(any("kafka.topic: iot_vehicle_raw_test" in message.text for message in messages))
            self.assertTrue(any("ts_write.pressure_level: medium" in message.text for message in messages))
            self.assertTrue(any("ts_query.dependency_mode: reuse" in message.text for message in messages))

    def test_ts_plan_orders_threshold_major_stage_runs(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ts", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ts"
            data["all"]["vars"]["compress_threshold"] = [1200, 4096]
            write_yaml(inventory, data)

            job_dir = run_task_job(root, inventory, plan_only=True)

            plan = load_yaml(job_dir / "resolved-plan.yaml")
            self.assertEqual(plan["benchmark"], "ts")
            self.assertEqual(
                [(run["compress_threshold"], run["stage"]) for run in plan["runs"]],
                [
                    (1200, "ts-write"),
                    (1200, "ts-query"),
                    (1200, "point-query"),
                    (4096, "ts-write"),
                    (4096, "ts-query"),
                    (4096, "point-query"),
                ],
            )
            self.assertEqual(plan["runs"][0]["target_table"], "iot_vehicle_raw_ct1200")
            self.assertEqual(plan["runs"][0]["kafka_topic"], "iot_vehicle_raw_test")

    def test_ts_single_stage_plan_traverses_all_thresholds(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ts", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ts"
            data["all"]["vars"]["compress_threshold"] = [1200, 4096]
            write_yaml(inventory, data)

            job_dir = run_task_job(root, inventory, plan_only=True, stage="ts-query")

            plan = load_yaml(job_dir / "resolved-plan.yaml")
            self.assertEqual([(run["compress_threshold"], run["stage"]) for run in plan["runs"]], [(1200, "ts-query"), (4096, "ts-query")])

    def test_mxgate_conf_uses_standard_toml_and_target_database_config(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ts", ["ym-mars3"])
            target_vars = data["all"]["children"]["ymatrix_mars3"]["vars"]
            target_vars.update(
                {
                    "db_host": "172.16.100.29",
                    "db_port": 5432,
                    "db_name": "tpccmars3",
                    "db_user": "zhangchen",
                    "db_password": "YMatrix@123",
                }
            )
            data["all"]["vars"]["kafka"]["topic"] = "automan_ts_verify"
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-write")

            _write_mxgate_conf(run.benchmark_dir / "mxgate.conf", task.targets[0], task.ts_config, run)

            text = (run.benchmark_dir / "mxgate.conf").read_text(encoding="utf-8")
            self.assertIn("[database]", text)
            self.assertIn('db-master-host = "172.16.100.29"', text)
            self.assertIn("db-master-port = 5432", text)
            self.assertIn('db-database = "tpccmars3"', text)
            self.assertIn('db-user = "zhangchen"', text)
            self.assertIn('db-password = "YMatrix@123"', text)
            self.assertIn("[source]", text)
            self.assertIn("[source.kafka]", text)
            self.assertIn('kafka-broker = "172.16.100.214:9092"', text)
            self.assertIn("[[source.kafka.topic]]", text)
            self.assertIn('topic = "automan_ts_verify"', text)
            self.assertIn('consumer-group = "automan_ts_verify_public.iot_vehicle_raw_ct1200"', text)
            self.assertIn('job = "job_text_to_public.iot_vehicle_raw_ct1200"', text)
            self.assertIn("[writer.stream]", text)
            self.assertIn("timing = true", text)
            self.assertIn('use-gzip = "no"', text)
            self.assertNotIn("bootstrap.servers", text)

    def test_mxgate_pid_commands_include_pid_and_lockfile_guard(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-write")

            for command in [
                _mxgate_status_command(task.ts_config, run),
                _mxgate_log_command(task.ts_config, run),
                _mxgate_stop_command(task.ts_config, run),
            ]:
                self.assertIn("--pid", command)
                self.assertIn("mxgate.pid", command)
                self.assertIn(".s.MXGATED.*.lock", command)
                self.assertIn("Refuse to operate mxgate pid", command)
                self.assertIn("mxgate.conf", command)
            self.assertIn("--lines 1000000", _mxgate_log_command(task.ts_config, run))

    def test_mxgate_timing_parser_uses_batch_window_without_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "mxgate.log"
            log.write_text(
                "\n".join(
                    [
                        "streaming.go:844: 2026-06-29:17:15:14.967 matrixgate:mxadmin:sdw18:495098-[INFO]:-[Writer.Stream] Insert 1623 rows to public.iot_vehicle_raw_ct1200 on con32817, slot [36], seq 1, timing (111.407271ms 2.39256439s 60.716087ms 24.733249ms 42.981555ms)",
                        "streaming.go:844: 2026-06-29:17:15:14.970 matrixgate:mxadmin:sdw18:495098-[INFO]:-[Writer.Stream] Insert 4243 rows to public.iot_vehicle_raw_ct1200 on con32816, slot [10], seq 1, timing (77.229716ms 18.763µs 2.472160187s 28.477439ms 102.479267ms)",
                        "streaming.go:844: 2026-06-29:17:15:29.945 matrixgate:mxadmin:sdw18:495098-[INFO]:-[Writer.Stream] Insert 44 rows to public.iot_vehicle_raw_ct1200 on con32827, slot [28], seq 1, timing (105.178846ms 17.333917897s 60.382338ms 24.109395ms 20.714106ms)",
                        "streaming.go:844: 2026-06-29:17:15:39.914 matrixgate:mxadmin:sdw18:495098-[INFO]:-[Writer.Stream] Insert 0 rows to public.iot_vehicle_raw_ct1200 on con32823, slot [17], seq 1, timing (108.087255ms 17.393943967s 10.009583619s 3.259849ms 1.165225ms)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            timing = _parse_mxgate_timing_log(log, "iot_vehicle_raw_ct1200")

            self.assertEqual(timing["mxgate_write_start"], "2026-06-29T17:15:12.289")
            self.assertEqual(timing["mxgate_write_end"], "2026-06-29T17:15:29.945")
            self.assertEqual(timing["mxgate_elapsed_seconds"], 17.655365)
            self.assertEqual(timing["mxgate_timing_rows"], 5910)
            self.assertEqual(timing["mxgate_timing_batches"], 4)
            self.assertEqual(timing["mxgate_timing_positive_batches"], 3)
            self.assertEqual(timing["mxgate_timing_zero_rows_batches"], 1)

    def test_execute_ts_job_creates_missing_job_state_and_preserves_failure(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ts", ["ym-mars3"])
            data["all"]["vars"]["job_id"] = "job-ts"
            data["all"]["vars"]["compress_threshold"] = [1200]
            data["all"]["vars"]["collectors"]["enabled"] = False
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            runs = build_ts_run_specs(root, "job-ts", task.targets[0], task.ts_config, "ts-write")

            execute_ts_job(
                root,
                "job-ts",
                task.targets[0],
                task.ts_config,
                runs,
                task.collectors,
                runner=lambda command, cwd, timeout, env=None: CommandResult(" ".join(command), 1, "", "DDL exploded"),
            )

            job_state = load_yaml(root / "runs" / "jobs" / "job-ts" / "job.json")
            self.assertEqual(job_state["status"], "failed")
            self.assertEqual(job_state["failed_runs"], 1)
            self.assertIn("DDL exploded", job_state["last_error"])
            self.assertTrue((runs[0].benchmark_dir / "mxgate.conf").exists())
            result = load_yaml(runs[0].run_dir / "result.json")
            self.assertEqual(result["status"], "failed")
            self.assertIn("DDL exploded", result["error"])

    def test_ts_ddl_is_partitioned_mars3_table_with_timestamp_first(self) -> None:
        ddl = _ts_table_ddl("iot_vehicle_raw_ct1200", 1200)
        timestamp_pos = ddl.index('"timeStamp" timestamp not null')
        vin_pos = ddl.index("vin text not null")

        self.assertLess(timestamp_pos, vin_pos)
        self.assertIn("using mars3", ddl)
        self.assertIn("distributed by (vin)", ddl)
        self.assertIn('order by (vin,"timeStamp")', ddl)
        self.assertIn('partition by range ("timeStamp")', ddl)
        self.assertIn("every(interval '1 day')", ddl)
        self.assertIn("default partition others", ddl)

    def test_ts_producer_uses_kafka_perf_with_payload_timestamp_before_vin(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-write")

            command = _producer_start_command(task.ts_config, run)

            self.assertIn("kafka-producer-perf-test.sh", command)
            self.assertIn("--payload-file", command)
            self.assertIn("%s|%s|%s|%s|payload-%s-%s", command)
            self.assertIn('"$ts" "$vin" "$(( i % 220 ))"', command)
            self.assertIn("--throughput 12500", command)
            self.assertIn("--num-records 3750000", command)

    def test_legacy_shell_ts_producer_checks_deadline_inside_large_batches(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            data = cli._compose_inventory(root, "ts", ["ym-mars3"])
            data["all"]["vars"]["ts_write"]["pressure_level"] = "legacy_shell"
            data["all"]["vars"]["ts_write"]["pressure_profiles"]["legacy_shell"] = {"producer_type": "shell"}
            write_yaml(inventory, data)
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-write")

            command = _producer_start_command(task.ts_config, run)

            self.assertIn('while [ "$batch" -lt 200000 ] && [ "$(date +%s)" -lt "$end" ]; do', command)
            self.assertIn('while [ "$chunk" -lt 5000 ] && [ "$batch" -lt 200000 ]; do', command)

    def test_ts_results_list_without_tpcc_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "runs/jobs/job-ts"
            run1 = root / "runs/job-ts-ymatrix_mars3-ct1200-ts-write"
            run2 = root / "runs/job-ts-ymatrix_mars3-ct1200-ts-query"
            write_yaml(
                job_dir / "resolved-plan.yaml",
                {
                    "job_id": "job-ts",
                    "benchmark": "ts",
                    "targets": [{"id": "ymatrix_mars3", "connection": {"db_host": "172.16.100.62"}}],
                    "runs": [
                        {
                            "run_id": run1.name,
                            "target_id": "ymatrix_mars3",
                            "stage": "ts-write",
                            "run_dir": str(run1),
                            "benchmark_result_dir": str(run1 / "benchmark/ts-write"),
                        },
                        {
                            "run_id": run2.name,
                            "target_id": "ymatrix_mars3",
                            "stage": "ts-query",
                            "run_dir": str(run2),
                            "benchmark_result_dir": str(run2 / "benchmark/ts-query"),
                        },
                    ],
                },
            )
            write_json(
                run1 / "result.json",
                {
                    "run_id": run1.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-write",
                    "status": "success",
                    "compress_threshold": 1200,
                    "target_table": "iot_vehicle_raw_ct1200",
                    "kafka_topic": "iot_vehicle_raw_test",
                    "table_data_size": "85 MB",
                    "pressure_level": "high",
                    "producer_target_qps": 200000,
                    "producer_actual_qps": 120.0,
                    "produced_messages": 1200,
                    "written_rows": 1000,
                    "duration_seconds": 10,
                    "mxgate_write_start": "2026-06-29T10:00:03.100",
                    "mxgate_write_end": "2026-06-29T10:00:08.100",
                    "mxgate_elapsed_seconds": 5.0,
                    "final_lag": 0,
                    "session_end": "2026-06-29T10:00:00",
                },
            )
            write_json(
                run2 / "result.json",
                {
                    "run_id": run2.name,
                    "target_id": "ymatrix_mars3",
                    "stage": "ts-query",
                    "status": "success",
                    "compress_threshold": 1200,
                    "target_table": "iot_vehicle_raw_ct1200",
                    "kafka_topic": "iot_vehicle_raw_test",
                    "table_data_size": "85 MB",
                    "query_count": 3,
                    "avg_ms": 12.3,
                    "p95_ms": 20.0,
                    "rows_returned": 90,
                    "session_end": "2026-06-29T10:01:00",
                },
            )

            rows = completed_result_rows(root, "job-ts", benchmark_type="ts")

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["stage"], "ts-query")
            self.assertEqual(rows[0]["table_data_size"], "85 MB")
            self.assertEqual(rows[1]["written_rows"], 1000)
            self.assertEqual(rows[1]["actual_qps"], 100.0)
            self.assertEqual(rows[1]["producer_actual_qps"], 120.0)
            self.assertEqual(rows[1]["session_start"], "2026-06-29T10:00:03.100")
            self.assertEqual(rows[1]["session_end"], "2026-06-29T10:00:08.100")
            self.assertEqual(rows[1]["elapsed_seconds"], 5.0)

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = show_completed_results(root, "job-ts", benchmark_type="ts")
            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("Stage", text)
            self.assertIn("Compress Threshold", text)
            self.assertIn("Table Data Size", text)
            self.assertIn("Pressure", text)
            self.assertIn("Produced Messages", text)
            self.assertIn("Actual QPS", text)
            self.assertIn("100.00", text)
            self.assertNotIn("Producer Actual QPS", text)
            self.assertIn("Final Lag", text)
            self.assertIn("Max Lag", text)
            self.assertNotIn("Target QPS", text)
            self.assertNotIn("Topic", text)
            self.assertNotIn("iot_vehicle_raw_test", text)
            self.assertIn("ts-query", text)
            self.assertNotIn("tpmC", text)

    def test_ts_table_data_size_uses_partition_tree_total_size(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-write")
            sql_seen: list[str] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                sql_seen.append(command[-1])
                return CommandResult("psql", 0, "85 MB\n", "")

            size = _table_data_size(task.targets[0], run, runner)

            self.assertEqual(size["table_data_size"], "85 MB")
            self.assertIsNone(size["table_data_size_bytes"])
            self.assertIn("pg_catalog.pg_partition_tree(c.oid)", sql_seen[0])
            self.assertIn("pg_catalog.pg_size_pretty", sql_seen[0])
            self.assertIn("'^(iot_vehicle_raw_ct1200)$'", sql_seen[0])

    def test_ts_range_samples_extend_max_timestamp_for_half_open_interval(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))
            task = load_task_definition(root, inventory)
            run = _ts_run(root, "verify-ts-ymatrix_mars3-ct1200-ts-query")
            sql_seen: list[str] = []

            def runner(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None) -> CommandResult:
                sql_seen.append(command[-1])
                return CommandResult("psql", 0, "VIN00000001|2026-06-29 09:47:19.898|2026-06-29 09:47:19.899\n", "")

            samples = _sample_ranges(task.targets[0], run, 1, runner)

            self.assertEqual(samples, [["VIN00000001", "2026-06-29 09:47:19.898", "2026-06-29 09:47:19.899"]])
            self.assertIn('max("timeStamp") + interval \'1 millisecond\'', sql_seen[0])

    def test_kafka_check_prints_apply_commands_from_config(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_ts_runtime_files(repo, root)
            inventory = root / "automan.yml"
            write_yaml(inventory, cli._compose_inventory(root, "ts", ["ym-mars3"]))
            output = io.StringIO()

            with redirect_stdout(output):
                failures = kafka_check(root, inventory, apply=False, ssh_factory=lambda *args: _FakeSSH())

            self.assertEqual(failures, 0)
            text = output.getvalue()
            self.assertIn("kafka-check --apply will execute", text)
            self.assertIn("--bootstrap-server localhost:9092", text)
            self.assertIn("--topic iot_vehicle_raw_test", text)
            self.assertIn("--partitions 36", text)


class _FakeSSH:
    def run(self, command: str, timeout: int = 120) -> CommandResult:
        return CommandResult(command, 0, "ok\n", "")


def _copy_ts_configure_templates(src: Path, dst: Path) -> None:
    for relative in [
        "conf/ts/base.yml",
        "conf/ts/targets/ym-mars3.yml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _copy_ts_runtime_files(src: Path, dst: Path) -> None:
    _copy_ts_configure_templates(src, dst)
    for relative in [
        "configs/database-profiles/ymatrix/mars3-master-only.yaml",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


def _ts_run(root: Path, run_id: str) -> TsRunSpec:
    run_dir = root / "runs" / run_id
    return TsRunSpec(
        run_id=run_id,
        target_id="ymatrix_mars3",
        stage="ts-write",
        compress_threshold=1200,
        target_table="iot_vehicle_raw_ct1200",
        kafka_topic="automan_ts_verify",
        work_dir=run_dir / "work",
        run_dir=run_dir,
        benchmark_dir=run_dir / "benchmark" / "ts-write",
        database_dir=run_dir / "database",
        logs_dir=run_dir / "logs",
        collector_dir=run_dir / "collectors",
    )


if __name__ == "__main__":
    unittest.main()
