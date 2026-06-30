# TPC-H YMatrix Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default Automan TPC-H backend with an offline vendored `ymatrix-data/TPC-H` backend that runs on each target database master while preserving Automan's MARS3 parameter matrix, job orchestration, collectors, listing, and reports.

**Architecture:** Add a focused YMatrix TPC-H backend adapter beside the existing TPC-H module. The adapter renders upstream variables for each Automan run, stages the vendored backend to `target.connection.db_host` via SSH/SFTP using `config_user`, executes the upstream stage mapping remotely, fetches artifacts, and normalizes results. The existing internal TPC-H runner remains as a legacy fallback but is no longer the default configured backend.

**Tech Stack:** Python 3.8, dataclasses, Paramiko SSH/SFTP, Ansible wrappers, YMatrix/MatrixDB `psql`, upstream `ymatrix-data/TPC-H` shell scripts, unittest.

---

## File Structure

- Create `automan_core/tpch_backend.py`: pure helpers for backend config defaults, stage mapping, upstream variable rendering, storage string rendering, remote path calculation, and result parsing.
- Create `automan_core/remote.py`: small SSH/SFTP utility wrappers built on Paramiko for command execution, recursive upload, and recursive download.
- Modify `automan_core/models.py`: add `TpchBackendConfig` and attach it to `TpchConfig`.
- Modify `automan_core/tpch.py`: parse backend config, select YMatrix backend by default, call the remote backend adapter for `tpch-load` and `tpch-query`, keep old internal functions behind `backend.type: internal`.
- Modify `automan_core/checks.py`: replace local dbgen/schema/query checks for the default backend with vendored-source and remote-master checks.
- Modify `conf/tpch/base.yml`: default to `tpch.backend.type: ymatrix-tpch`, `source_dir: tools/ymatrix-tpch`, `load_data_type: mxgate`, and remove dependence on `tools/tpch-dbgen` for normal runs.
- Create `tools/ymatrix-tpch/`: vendored offline upstream source fixed to commit `11bc7c50910f4bdb732a4615e7711beaa4914965`.
- Modify `tests/test_tpch.py`: add tests for backend config defaults, variable rendering, stage mapping, remote execution host choice, and result normalization.
- Modify `tests/test_checks.py`: add tests for YMatrix backend readiness checks.
- Modify `tests/test_report.py` only if normalized result fields need report coverage beyond the existing TPC-H report tests.

## Task 1: Add Backend Config Model And Defaults

**Files:**
- Modify: `automan_core/models.py`
- Modify: `automan_core/tpch.py`
- Modify: `conf/tpch/base.yml`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing config default test**

Add this test to `tests/test_tpch.py`:

```python
def test_tpch_config_defaults_to_ymatrix_backend(self) -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_tpch_runtime_files(repo, root)
        inventory = root / "automan.yml"
        data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
        write_yaml(inventory, data)

        task = load_task_definition(root, inventory)

        self.assertEqual(task.tpch_config.backend.type, "ymatrix-tpch")
        self.assertEqual(task.tpch_config.backend.source_dir, "tools/ymatrix-tpch")
        self.assertEqual(task.tpch_config.backend.remote_dir, "runs/{run_id}/ymatrix-tpch")
        self.assertEqual(task.tpch_config.backend.database_type, "matrixdb")
        self.assertEqual(task.tpch_config.backend.access_method, "mars3")
        self.assertEqual(task.tpch_config.backend.load_data_type, "mxgate")
        self.assertEqual(task.tpch_config.backend.optimizer, "off")
        self.assertTrue(task.tpch_config.backend.preheating_data)
        self.assertFalse(task.tpch_config.backend.explain_analyze)
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_tpch_config_defaults_to_ymatrix_backend
```

Expected: FAIL with an attribute error because `TpchConfig` has no `backend`.

- [ ] **Step 3: Add dataclass and loader support**

Add to `automan_core/models.py`:

```python
@dataclass(frozen=True)
class TpchBackendConfig:
    type: str = "ymatrix-tpch"
    source_dir: str = "tools/ymatrix-tpch"
    remote_dir: str = "runs/{run_id}/ymatrix-tpch"
    database_type: str = "matrixdb"
    access_method: str = "mars3"
    load_data_type: str = "mxgate"
    optimizer: str = "off"
    preheating_data: bool = True
    explain_analyze: bool = False
    greenplum_path: str = ""
```

Add `backend: TpchBackendConfig` to `TpchConfig`.

In `automan_core/tpch.py`, import `TpchBackendConfig`, parse `raw_backend = dict(raw.get("backend", {}) or {})`, and pass:

```python
backend=TpchBackendConfig(
    type=str(raw_backend.get("type", "ymatrix-tpch")),
    source_dir=str(raw_backend.get("source_dir", "tools/ymatrix-tpch")),
    remote_dir=str(raw_backend.get("remote_dir", "runs/{run_id}/ymatrix-tpch")),
    database_type=str(raw_backend.get("database_type", "matrixdb")),
    access_method=str(raw_backend.get("access_method", "mars3")),
    load_data_type=str(raw_backend.get("load_data_type", "mxgate")),
    optimizer=str(raw_backend.get("optimizer", "off")),
    preheating_data=_bool(raw_backend.get("preheating_data", True)),
    explain_analyze=_bool(raw_backend.get("explain_analyze", False)),
    greenplum_path=str(raw_backend.get("greenplum_path", "")),
)
```

- [ ] **Step 4: Update default inventory**

In `conf/tpch/base.yml`, add:

```yaml
      backend:
        type: ymatrix-tpch
        source_dir: tools/ymatrix-tpch
        remote_dir: runs/{run_id}/ymatrix-tpch
        database_type: matrixdb
        access_method: mars3
        load_data_type: mxgate
        optimizer: off
        preheating_data: true
        explain_analyze: false
        greenplum_path: ""
```

Keep `data_prepare` for legacy compatibility, but default execution will use `backend.type`.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest tests.test_tpch
```

Expected: PASS after updating older tests that assert `data_prepare` defaults so they still pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add automan_core/models.py automan_core/tpch.py conf/tpch/base.yml tests/test_tpch.py
git commit -m "feat: add tpch backend config"
```

## Task 2: Vendor Offline YMatrix TPC-H Source

**Files:**
- Create: `tools/ymatrix-tpch/`
- Create: `tools/ymatrix-tpch/AUTOMAN_VENDOR.md`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Copy upstream source into `tools/ymatrix-tpch`**

Use the already cloned source at `%TEMP%/ymatrix-tpch` if present, or clone once during development. The final repository must contain the files under `tools/ymatrix-tpch` so runtime does not need network access.

Expected top-level files after copy:

```text
tools/ymatrix-tpch/00_compile_tpch
tools/ymatrix-tpch/01_gen_data
tools/ymatrix-tpch/02_init
tools/ymatrix-tpch/03_ddl
tools/ymatrix-tpch/04_load
tools/ymatrix-tpch/05_sql
tools/ymatrix-tpch/06_single_user_reports
tools/ymatrix-tpch/07_multi_user
tools/ymatrix-tpch/08_multi_user_reports
tools/ymatrix-tpch/functions.sh
tools/ymatrix-tpch/README.md
tools/ymatrix-tpch/rollout.sh
tools/ymatrix-tpch/tpch.sh
```

- [ ] **Step 2: Add vendor metadata**

Create `tools/ymatrix-tpch/AUTOMAN_VENDOR.md`:

```markdown
# Vendored YMatrix TPC-H

Source: https://github.com/ymatrix-data/TPC-H
Commit: 11bc7c50910f4bdb732a4615e7711beaa4914965
Date: 2023-11-07 13:48:59 +0800

This directory is vendored so Automan TPC-H runs without internet access.
Runtime code must not git clone or pull this backend.
```

- [ ] **Step 3: Add vendor presence test**

Add to `tests/test_tpch.py`:

```python
def test_vendored_ymatrix_tpch_backend_is_present(self) -> None:
    repo = Path(__file__).resolve().parents[1]
    backend = repo / "tools/ymatrix-tpch"

    self.assertTrue((backend / "tpch.sh").exists())
    self.assertTrue((backend / "rollout.sh").exists())
    self.assertTrue((backend / "00_compile_tpch/dbgen/release.h").exists())
    text = (backend / "00_compile_tpch/dbgen/release.h").read_text(encoding="utf-8")
    self.assertIn("#define VERSION 2", text)
    self.assertIn("#define RELEASE 17", text)
    self.assertIn("#define PATCH 0", text)
    self.assertIn("#define BUILD 0", text)
    self.assertIn("11bc7c50910f4bdb732a4615e7711beaa4914965", (backend / "AUTOMAN_VENDOR.md").read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run vendor test**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_vendored_ymatrix_tpch_backend_is_present
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add tools/ymatrix-tpch tests/test_tpch.py
git commit -m "chore: vendor ymatrix tpch backend"
```

## Task 3: Add Backend Helper Module

**Files:**
- Create: `automan_core/tpch_backend.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing helper tests**

Add tests for stage flags, storage rendering, remote path rendering, and variable rendering:

```python
def test_ymatrix_backend_stage_flags_and_storage_rendering(self) -> None:
    from automan_core.tpch_backend import render_mars3_storage, stage_flags

    self.assertEqual(render_mars3_storage(1200), "USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=1200)")
    self.assertEqual(stage_flags("tpch-load")["RUN_LOAD"], "true")
    self.assertEqual(stage_flags("tpch-load")["RUN_SQL"], "false")
    self.assertEqual(stage_flags("tpch-query")["RUN_LOAD"], "false")
    self.assertEqual(stage_flags("tpch-query")["RUN_SQL"], "true")
```

```python
def test_ymatrix_backend_variables_use_target_db_host(self) -> None:
    from automan_core.tpch_backend import render_backend_variables, remote_backend_dir

    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_tpch_runtime_files(repo, root)
        inventory = root / "automan.yml"
        data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
        data["all"]["vars"]["job_id"] = "job-tpch"
        data["all"]["children"]["ymatrix_mars3"]["vars"]["db_host"] = "10.9.8.7"
        data["all"]["children"]["ymatrix_mars3"]["vars"]["config_workdir"] = "/home/mxadmin/automan"
        write_yaml(inventory, data)
        task = load_task_definition(root, inventory)
        runs = build_tpch_run_specs(root, "job-tpch", task.targets, task.tpch_config, "tpch-load")

        remote_dir = remote_backend_dir(task.targets[0], task.tpch_config.backend, runs[0])
        variables = render_backend_variables(task.targets[0], task.tpch_config.backend, runs[0])

        self.assertEqual(remote_dir, f"/home/mxadmin/automan/runs/{runs[0].run_id}/ymatrix-tpch")
        self.assertIn('PGHOST="10.9.8.7"', variables)
        self.assertIn('GEN_DATA_SCALE="1"', variables)
        self.assertIn('LOAD_DATA_TYPE="mxgate"', variables)
        self.assertIn('SMALL_STORAGE="USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=1200)"', variables)
        self.assertIn('RUN_LOAD="true"', variables)
        self.assertIn('RUN_SQL="false"', variables)
```

- [ ] **Step 2: Create implementation**

Create `automan_core/tpch_backend.py` with these public functions:

```python
def stage_flags(stage: str) -> dict[str, str]: ...
def render_mars3_storage(compress_threshold: int | None) -> str: ...
def remote_backend_dir(target: Target, backend: TpchBackendConfig, run: TpchRunSpec) -> str: ...
def render_backend_variables(target: Target, backend: TpchBackendConfig, run: TpchRunSpec) -> str: ...
def normalize_backend_result(run: TpchRunSpec, stage: str, command_result: CommandResult, remote_dir: str, local_artifact_dir: Path, started: str, ended: str) -> dict[str, Any]: ...
```

The shell variable renderer must quote values safely with double quotes and escaped `\`, `"`, `$`, and backticks. It must include all upstream switches from the spec.

- [ ] **Step 3: Run helper tests**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_ymatrix_backend_stage_flags_and_storage_rendering tests.test_tpch.TpchBenchmarkTest.test_ymatrix_backend_variables_use_target_db_host
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```bash
git add automan_core/tpch_backend.py tests/test_tpch.py
git commit -m "feat: render ymatrix tpch backend variables"
```

## Task 4: Add Remote Upload/Download Utilities

**Files:**
- Create: `automan_core/remote.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add fakeable remote client interface test**

Add a test that imports the public names and verifies the module exposes callable helpers:

```python
def test_remote_helpers_are_available_for_backend(self) -> None:
    from automan_core.remote import RemoteClient

    self.assertTrue(hasattr(RemoteClient, "run"))
    self.assertTrue(hasattr(RemoteClient, "upload_dir"))
    self.assertTrue(hasattr(RemoteClient, "download_dir"))
```

- [ ] **Step 2: Implement `RemoteClient`**

Create `automan_core/remote.py` with:

```python
class RemoteClient:
    def __init__(self, host: str, port: int, user: str, password: str) -> None: ...
    def run(self, command: str, timeout: int = 120) -> CommandResult: ...
    def upload_dir(self, local_dir: Path, remote_dir: str) -> CommandResult: ...
    def download_dir(self, remote_dir: str, local_dir: Path) -> CommandResult: ...
```

Use Paramiko. `upload_dir` must recursively create directories and upload files. `download_dir` must recursively copy remote files to a local directory. Return `CommandResult` instead of raising for SSH/SFTP failures.

- [ ] **Step 3: Run test**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_remote_helpers_are_available_for_backend
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```bash
git add automan_core/remote.py tests/test_tpch.py
git commit -m "feat: add remote file transfer helper"
```

## Task 5: Execute YMatrix Backend For TPC-H Runs

**Files:**
- Modify: `automan_core/tpch.py`
- Modify: `automan_core/tpch_backend.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add fake remote execution tests**

Add tests that inject a fake backend executor and verify:

- `tpch-load` no longer calls local DDL/copy SQL when `backend.type=ymatrix-tpch`
- backend execution host is `target.connection.db_host`
- normalized result contains `backend_type`, `remote_backend_dir`, `schema: tpch`, and upstream artifacts

Use a fake object:

```python
class FakeYMatrixBackend:
    def __init__(self) -> None:
        self.calls = []

    def run(self, root, target, config, run):
        self.calls.append((target.connection.db_host, run.stage, run.compress_threshold))
        return {
            "run_id": run.run_id,
            "target_id": run.target_id,
            "stage": run.stage,
            "status": "success",
            "error": None,
            "backend_type": "ymatrix-tpch",
            "remote_backend_dir": f"{target.connection.remote_workdir}/runs/{run.run_id}/ymatrix-tpch",
            "schema": "tpch",
            "scale_factor": run.scale_factor,
            "compress_threshold": run.compress_threshold,
            "query_streams": run.query_streams,
            "elapsed_seconds": 1.0,
            "upstream_artifacts": {"local_dir": str(run.benchmark_dir / "upstream")},
        }
```

- [ ] **Step 2: Add backend injection to `execute_tpch_job`**

Extend `execute_tpch_job` signature with:

```python
backend_executor: Any | None = None
```

Thread it to `_execute_tpch_run` and `_run_tpch_load/_run_tpch_query`.

- [ ] **Step 3: Select backend in load/query**

In `_run_tpch_load` and `_run_tpch_query`, add:

```python
if config.backend.type == "ymatrix-tpch":
    executor = backend_executor or YMatrixTpchBackend()
    return executor.run(root, target, config, run)
```

Retain the old code path for `backend.type == "internal"`.

- [ ] **Step 4: Implement `YMatrixTpchBackend`**

In `automan_core/tpch_backend.py`, implement a class that:

1. Resolves local source directory.
2. Resolves remote backend directory from `db_host` target config.
3. Uploads vendored source to remote directory.
4. Writes `tpch_variables.sh` remotely using rendered variables.
5. Runs `cd <remote_dir> && bash ./tpch.sh` with `PGPASSWORD` in the environment.
6. Downloads remote artifacts to `run.benchmark_dir / "upstream"`.
7. Returns a normalized result.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest tests.test_tpch
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add automan_core/tpch.py automan_core/tpch_backend.py tests/test_tpch.py
git commit -m "feat: run tpch through ymatrix backend"
```

## Task 6: Upgrade TPC-H Checks For YMatrix Backend

**Files:**
- Modify: `automan_core/checks.py`
- Test: `tests/test_checks.py`

- [ ] **Step 1: Add backend check tests**

Add tests that assert:

- missing `tools/ymatrix-tpch/tpch.sh` fails
- remote checks use `db_host`, not hardcoded `172.16.100.29`
- remote `config_workdir` writability is checked
- required remote commands include `psql`, `make`, `gcc`, `ssh`, and `scp`

- [ ] **Step 2: Implement checks**

In `check_tpch_readiness`, when `config.backend.type == "ymatrix-tpch"`:

- Check local source directory and `tpch.sh`.
- For each target, create an SSH runner to `target.connection.db_host` using `target.connection.ssh_port`, `ssh_user`, and `ssh_password`.
- Check `config_workdir` writability.
- Check required remote commands.
- Run remote psql connectivity with `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`.
- For MatrixDB `mxgate`, run `select count(*) from gp_segment_configuration;`.

- [ ] **Step 3: Run checks tests**

Run:

```bash
python -m unittest tests.test_checks
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```bash
git add automan_core/checks.py tests/test_checks.py
git commit -m "feat: check ymatrix tpch backend readiness"
```

## Task 7: Preserve Report/List Compatibility

**Files:**
- Modify: `automan_core/report.py` if needed
- Modify: `automan_core/list_results.py` if needed
- Test: `tests/test_report.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add normalized backend result test**

Create a TPC-H job result with:

```json
{
  "backend_type": "ymatrix-tpch",
  "schema": "tpch",
  "remote_backend_dir": "/home/mxadmin/automan/runs/run/ymatrix-tpch",
  "upstream_artifacts": {"local_dir": "runs/run/benchmark/tpch-query/upstream"}
}
```

Assert list/report output still shows TPC-H fields and does not regress to TPC-C labels.

- [ ] **Step 2: Update renderers only when failing**

If the existing report/list code ignores extra fields and tests pass, do not change it. If the output misses backend evidence, add concise TPC-H rows for backend type, schema, and remote backend directory.

- [ ] **Step 3: Run report/list tests**

Run:

```bash
python -m unittest tests.test_report tests.test_list_results tests.test_tpch
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```bash
git add automan_core/report.py automan_core/list_results.py tests/test_report.py tests/test_tpch.py
git commit -m "feat: report ymatrix tpch backend metadata"
```

## Task 8: Local Regression Suite

**Files:**
- Runtime only

- [ ] **Step 1: Run focused regression**

Run:

```bash
python -m unittest tests.test_tpch tests.test_checks tests.test_cli_contract tests.test_report tests.test_list_results
```

Expected: PASS.

- [ ] **Step 2: Run broader benchmark suite**

Run:

```bash
python -m unittest tests.test_tpch tests.test_checks tests.test_cli_contract tests.test_report tests.test_list_results tests.test_ap tests.test_ts tests.test_executor tests.test_task_runner
```

Expected: PASS.

- [ ] **Step 3: Commit any test fixes**

If tests required fixes, commit with:

```bash
git add <changed-files>
git commit -m "test: stabilize ymatrix tpch backend"
```

## Task 9: Remote Verification On 143 And Target DB Master

**Files:**
- Runtime on `172.16.100.143` and target `db_host`

- [ ] **Step 1: Sync current worktree to 143**

Copy changed Automan files and `tools/ymatrix-tpch` to `/root/automan` on `172.16.100.143`.

- [ ] **Step 2: Generate current test inventory**

On 143:

```bash
cd /root/automan
./configure -t tpch -c ym-mars3 -o runs/tpch-29-sf10-ct1200-ymatrix.yml
python3.8 - <<'PY'
from pathlib import Path
from automan_core.config import load_yaml, write_yaml
p = Path("runs/tpch-29-sf10-ct1200-ymatrix.yml")
d = load_yaml(p)
d["all"]["vars"]["compress_threshold"] = [1200]
d["all"]["vars"]["tpch"]["scale_factors"] = [10]
v = d["all"]["children"]["ymatrix_mars3"]["vars"]
v["db_host"] = "172.16.100.29"
v["db_port"] = 5432
v["db_name"] = "tpch"
v["db_user"] = "zhangchen"
v["db_password"] = "YMatrix@123"
v["config_user"] = "mxadmin"
v["config_password"] = "mxadmin"
v["config_workdir"] = "/home/mxadmin/automan"
write_yaml(p, d)
PY
```

- [ ] **Step 3: Run check**

Run:

```bash
./check.yml -i runs/tpch-29-sf10-ct1200-ymatrix.yml
```

Expected: check output proves vendored backend exists, remote DB master is reachable through `db_host`, remote workdir is writable, remote `psql/make/gcc/ssh/scp` are present, and MatrixDB metadata is queryable.

- [ ] **Step 4: Run load stage**

Run:

```bash
./tpch.yml -i runs/tpch-29-sf10-ct1200-ymatrix.yml -s tpch-load
```

Expected: `tpch-load` succeeds through upstream compile/gen/init/ddl/load. Remote artifacts show `tpch_variables.sh`, upstream logs, `LOAD_DATA_TYPE="mxgate"`, and MARS3 storage with `compress_threshold=1200`.

- [ ] **Step 5: Verify database state**

Run against the target DB:

```bash
PGPASSWORD='YMatrix@123' psql -h 172.16.100.29 -p 5432 -U zhangchen -d tpch -Atc "select schemaname || '.' || tablename from pg_tables where schemaname='tpch' order by tablename;"
PGPASSWORD='YMatrix@123' psql -h 172.16.100.29 -p 5432 -U zhangchen -d tpch -Atc "select schemaname || '.' || tablename || ':' || n_live_tup from pg_stat_user_tables where schemaname='tpch' order by tablename;"
PGPASSWORD='YMatrix@123' psql -h 172.16.100.29 -p 5432 -U zhangchen -d tpch -Atc "select count(*) from pg_indexes where schemaname='tpch' and indexdef like '%mars3_brin%';"
```

Expected: eight `tpch` tables, non-zero row counts for SF10, and MARS3 BRIN indexes from upstream DDL.

- [ ] **Step 6: Run query stage**

Run:

```bash
./tpch.yml -i runs/tpch-29-sf10-ct1200-ymatrix.yml -s tpch-query
```

Expected: upstream generated SQL runs through `05_sql/rollout.sh`; no internal `benchmarks/tpch/queries/standard` SQL is used.

- [ ] **Step 7: Verify Automan outputs**

Run:

```bash
./automan list -t tpch
./automan report --job <job_id>
```

Expected: list/report show TPC-H rows with backend metadata and no TPC-C labels.

- [ ] **Step 8: Commit verification fixes**

If remote verification exposes implementation bugs, fix them locally, re-run local tests, sync again, and commit fixes before claiming completion.

## Self-Review

Spec coverage:

- Offline vendored backend: Tasks 2 and 6.
- No hardcoded `172.16.100.29`: Tasks 3, 5, 6, and 9 validate `db_host`.
- MARS3 parameter matrix retained: Tasks 1, 3, and 5.
- GitHub load method retained: Tasks 3, 5, 6, and 9 use `LOAD_DATA_TYPE=mxgate`.
- Stage mapping: Tasks 3 and 5.
- `tpch` schema and upstream queries: Tasks 3, 5, and 9.
- Results/list/report: Tasks 5 and 7.
- Real verification: Task 9.

Placeholder scan: no `TODO` or `TBD` steps are intentionally left.

Type consistency: `TpchBackendConfig`, `remote_backend_dir`, `render_backend_variables`, `YMatrixTpchBackend`, and `RemoteClient` are named consistently across tasks.
