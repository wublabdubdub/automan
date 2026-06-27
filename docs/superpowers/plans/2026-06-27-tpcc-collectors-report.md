# TPC-C Collectors Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build first-class TPC-C resource/perf collection and objective performance reporting for Automan.

**Architecture:** Keep the Pigsty-style YAML inventory as the single source of truth. `automan check` validates database connectivity plus all configured collection tools and permissions before any benchmark runs. `automan run` wraps BenchmarkSQL phases with host collectors and `automan report` renders objective benchmark, resource, and perf artifacts for later agent analysis.

**Tech Stack:** Python 3.8+, Ansible playbooks, BenchmarkSQL, PostgreSQL/YMatrix-compatible `psql`, Linux `sysstat` tools, Linux `perf record/script/report`, Paramiko for SSH/SFTP collection from database hosts.

---

## File Structure

- Modify `conf/tpcc/*.yml`: add commented `collectors` blocks with system interval, host roles, and `perf record` settings.
- Modify `configs/collectors/default.yaml`: replace old `perf stat` default with `perf record` defaults.
- Modify `automan_core/models.py`: add `CollectorConfig`, `SystemCollectorConfig`, and `PerfCollectorConfig`.
- Modify `automan_core/task_runner.py`: parse `collectors` from inventory/legacy YAML, validate frequencies and host roles, pass config into campaign execution and plan files.
- Create `automan_core/checks.py`: implement local and SSH preflight checks for system tools, perf permissions, writable collection directories, and DB connectivity.
- Create `automan_core/collectors.py`: start/stop local and SSH collectors around run phases; fetch remote files back into the run directory.
- Modify `automan_core/executor.py`: wrap `runDatabaseBuild.sh` and `runBenchmark.sh` with collectors, making `perf record` default to benchmark phase only.
- Modify `automan_core/report.py`: parse BenchmarkSQL result logs, summarize metrics files, index perf artifacts, write `report.md` and `agent-context.json`.
- Modify `templates/report.md.j2`: objective report only, no recommendations.
- Modify `roles/*` and `docs/user-guide-zh.md`: document new check contract, collection outputs, and permissions.
- Add tests under `tests/`: config parsing, check failures, collector lifecycle with fakes, BenchmarkSQL parser, report artifact indexing.

## Tasks

### Task 1: Collector Configuration Model

**Files:**
- Modify: `automan_core/models.py`
- Modify: `automan_core/task_runner.py`
- Modify: `conf/tpcc/pg.yml`
- Modify: `conf/tpcc/ymatrix-heap.yml`
- Modify: `conf/tpcc/ymatrix-mars3.yml`
- Modify: `conf/tpcc/pg-vs-ymatrix.yml`
- Modify: `configs/collectors/default.yaml`
- Test: `tests/test_inventory_config.py`

- [ ] Add dataclasses:

```python
@dataclass(frozen=True)
class SystemCollectorConfig:
    enabled: bool = True
    interval_seconds: int = 1
    host_roles: list[str] = field(default_factory=lambda: ["database"])
    tools: list[str] = field(default_factory=lambda: ["vmstat", "iostat", "pidstat", "mpstat"])


@dataclass(frozen=True)
class PerfCollectorConfig:
    enabled: bool = True
    phases: list[str] = field(default_factory=lambda: ["runBenchmark.sh"])
    host_roles: list[str] = field(default_factory=lambda: ["database"])
    frequency: int = 99
    call_graph: str = "fp"
    record_scope: str = "system"


@dataclass(frozen=True)
class CollectorConfig:
    enabled: bool = True
    system: SystemCollectorConfig = field(default_factory=SystemCollectorConfig)
    perf: PerfCollectorConfig = field(default_factory=PerfCollectorConfig)
```

- [ ] Extend `TaskDefinition` with `collectors: CollectorConfig`.
- [ ] Parse top-level `collectors` from inventory `all.vars.collectors`; legacy YAML may use top-level `collectors`.
- [ ] Validate `interval_seconds > 0`, `frequency > 0`, `call_graph in {"fp", "dwarf", "lbr", "none"}`, and host roles in `{"execution", "database"}`.
- [ ] Add comments to every `conf/tpcc/*.yml` template explaining defaults: system metrics every 1 second, perf record at 99 Hz, benchmark phase only.
- [ ] Update tests to assert `pg.yml` loads `collectors.system.interval_seconds == 1` and `collectors.perf.frequency == 99`.

### Task 2: Check Gate for Collection Readiness

**Files:**
- Create: `automan_core/checks.py`
- Modify: `automan_core/cli.py`
- Test: `tests/test_checks.py`

- [ ] Move current DB connectivity check from `cli.py` into `checks.check_database_connectivity`.
- [ ] Add local command checks for `vmstat`, `iostat`, `pidstat`, `mpstat`, and `perf`.
- [ ] Add SSH command checks for configured database hosts when `collectors.*.host_roles` includes `database` and the database host is not the local execution host.
- [ ] Run this perf permission probe on each required host:

```bash
tmpdir=$(mktemp -d /tmp/automan-perf-check.XXXXXX)
perf record -F 99 -a -g -o "$tmpdir/perf.data" -- sleep 0.2
perf script -i "$tmpdir/perf.data" >/dev/null
perf report --stdio -i "$tmpdir/perf.data" >/dev/null
rm -rf "$tmpdir"
```

- [ ] On failure, print exact remediation hints including:

```text
required: install perf and run as root, or grant CAP_PERFMON/CAP_SYS_ADMIN as appropriate
check: cat /proc/sys/kernel/perf_event_paranoid
check: cat /proc/sys/kernel/kptr_restrict
example temporary fix: sysctl kernel.perf_event_paranoid=-1
```

- [ ] Make `automan check` return non-zero if any configured collector check fails.
- [ ] Add tests that simulate missing `perf` and assert the message includes the permission/tool needed.

### Task 3: Runtime Collectors

**Files:**
- Create: `automan_core/collectors.py`
- Modify: `automan_core/executor.py`
- Test: `tests/test_collectors.py`
- Test: `tests/test_executor.py`

- [ ] Implement local collectors using `subprocess.Popen`.
- [ ] Implement SSH collectors using Paramiko `exec_command` and SFTP fetch.
- [ ] Write files under:

```text
runs/<run_id>/collectors/<phase>/<host_role>/
  system/vmstat.log
  system/iostat-x.log
  system/pidstat-durh.log
  system/mpstat-P-ALL.log
  perf/perf.data
  perf/perf.script.txt
  perf/perf.report.txt
  manifest.json
```

- [ ] Start system collectors for build and benchmark phases when enabled.
- [ ] Start `perf record` only for phases listed in `collectors.perf.phases`, default `runBenchmark.sh`.
- [ ] Stop perf with `SIGINT`, then run `perf script` and `perf report --stdio`.
- [ ] If stop/export fails, mark the phase failed with the exact collector error instead of producing a false successful run.
- [ ] Add unit tests with fake process runners proving start/stop order around `runBenchmark.sh`.

### Task 4: Objective Performance Report

**Files:**
- Modify: `automan_core/report.py`
- Modify: `templates/report.md.j2`
- Test: `tests/test_report.py`

- [ ] Parse BenchmarkSQL logs for:

```text
Measured tpmC (NewOrders)
Measured tpmTOTAL
Session Start
Session End
```

- [ ] Include one table per run with target, warehouse, terminals, run minutes, status, tpmC, tpmTOTAL, and elapsed time if present.
- [ ] Include objective resource artifact summary: file paths, sample counts when parseable, and collection status.
- [ ] Include perf artifact summary: `perf.data`, `perf.script.txt`, `perf.report.txt`, file sizes, and whether files are empty.
- [ ] Generate `runs/campaigns/<campaign>/report/agent-context.json` containing plan, progress, parsed run results, log paths, collector paths, and perf paths.
- [ ] Remove recommendation/replay language from report content.

### Task 5: Playbooks and Documentation

**Files:**
- Modify: `roles/db_check/tasks/main.yml`
- Modify: `roles/collector/tasks/main.yml`
- Modify: `docs/user-guide-zh.md`
- Modify: `README.md`

- [ ] Document the exact preflight order:

```text
validate -> check DB -> check collector tools -> check perf record permission -> param manual commands -> run
```

- [ ] Document that Automan never modifies database parameters.
- [ ] Document collector outputs and that default frequency is system 1s and perf 99 Hz.
- [ ] Make `roles/collector` state that runtime collection is handled by the Python executor, while Ansible only archives generated artifacts.

### Task 6: Verification and Sync

**Files:**
- Modify as needed only for failing tests.

- [ ] Run local tests:

```bash
python -m unittest discover -s tests -v
```

- [ ] Build/check on Linux execution host:

```bash
python3.8 -m unittest discover -s tests -v
ansible-playbook --syntax-check -i conf/tpcc/pg.yml check.yml
ansible-playbook --syntax-check -i conf/tpcc/pg.yml tpcc.yml
```

- [ ] Run a non-destructive readiness check with a temp inventory containing passwords:

```bash
./check.yml -i /tmp/automan-pg-check.yml
```

- [ ] Commit and sync to GitHub and `/root/automan`.

## Self-Review

- Spec coverage: check gate, perf record, objective report, frequency config, and no DB parameter execution are covered.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation placeholders remain.
- Type consistency: config class names and task fields are consistent across tasks.
