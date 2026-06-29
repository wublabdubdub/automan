# TPC-H First-Class Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TPC-H run like TPC-C in Automan, including automatic real dbgen data preparation, a top-level `tpch.yml` entry point, stronger preflight checks, and benchmark-aware reports.

**Architecture:** Extend the existing TPC-H module instead of creating a parallel runner. Add a focused data preparation unit inside `automan_core/tpch.py`, expose its config through `TpchConfig`, call it before `tpch-load`, and keep playbooks as thin wrappers around `python -m automan_core run`.

**Tech Stack:** Python 3.8, argparse, Ansible playbooks/roles, Paramiko-based checks and collectors, PostgreSQL/YMatrix via `psql`, TPC-H dbgen built with `make`.

---

### Task 1: Model And Template Data Preparation

**Files:**
- Modify: `automan_core/models.py`
- Modify: `automan_core/tpch.py`
- Modify: `conf/tpch/base.yml`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing tests for `tpch.data_prepare` loading**

Add a test that composes a TPC-H inventory and asserts defaults:

```python
def test_tpch_config_loads_data_prepare_defaults(self) -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_tpch_runtime_files(repo, root)
        inventory = root / "automan.yml"
        data = cli._compose_inventory(root, "tpch", ["ym-mars3"])
        write_yaml(inventory, data)

        task = load_task_definition(root, inventory)

        self.assertEqual(task.tpch_config.data_prepare.mode, "auto")
        self.assertEqual(task.tpch_config.data_prepare.generator, "dbgen")
        self.assertEqual(task.tpch_config.data_prepare.source_dir, "tools/tpch-dbgen")
        self.assertEqual(task.tpch_config.data_prepare.build_command, "make")
        self.assertEqual(task.tpch_config.data_prepare.dbgen_command, "./dbgen")
        self.assertFalse(task.tpch_config.data_prepare.force)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_tpch_config_loads_data_prepare_defaults
```

Expected: FAIL because `TpchConfig` has no `data_prepare`.

- [ ] **Step 3: Add dataclass and loader support**

Add `TpchDataPrepareConfig` to `automan_core/models.py` and add `data_prepare` to `TpchConfig`.

In `automan_core/tpch.py`, parse `tpch.data_prepare` with defaults:

```python
raw_prepare = dict(raw.get("data_prepare", {}) or {})
data_prepare=TpchDataPrepareConfig(
    mode=str(raw_prepare.get("mode", "auto")),
    generator=str(raw_prepare.get("generator", "dbgen")),
    source_dir=str(raw_prepare.get("source_dir", "tools/tpch-dbgen")),
    build_command=str(raw_prepare.get("build_command", "make")),
    dbgen_command=str(raw_prepare.get("dbgen_command", "./dbgen")),
    force=bool(raw_prepare.get("force", False)),
)
```

- [ ] **Step 4: Add template defaults**

Update `conf/tpch/base.yml` under `tpch:` with:

```yaml
      data_prepare:
        mode: auto
        generator: dbgen
        source_dir: tools/tpch-dbgen
        build_command: make
        dbgen_command: ./dbgen
        force: false
```

- [ ] **Step 5: Run focused TPC-H tests**

Run:

```bash
python -m unittest tests.test_tpch
```

Expected: PASS.

### Task 2: Implement Real Data Readiness And Generation

**Files:**
- Modify: `automan_core/tpch.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing tests for non-empty data and dbgen invocation**

Add tests that:

- Create all eight empty `.tbl` files and assert `tpch-load` fails before DDL.
- Configure `data_prepare.mode: auto`, create a fake `tools/tpch-dbgen/dbgen` script that emits non-empty `.tbl` files, and assert load succeeds.

The fake dbgen script should parse `-s 10` and write all eight files in its current directory.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m unittest tests.test_tpch.TpchBenchmarkTest.test_execute_tpch_load_empty_data_fails tests.test_tpch.TpchBenchmarkTest.test_execute_tpch_load_auto_generates_missing_data
```

Expected: FAIL because empty files are accepted and auto generation is absent.

- [ ] **Step 3: Add data status helpers**

In `automan_core/tpch.py`, add helpers:

```python
def tpch_data_status(root: Path, config: TpchConfig, scale_factor: int) -> dict[str, Any]:
    data_dir = _tpch_data_dir(root, config, scale_factor)
    files = {table: tbl_path(data_dir, table) for table in TPCH_LOAD_ORDER}
    missing = [table for table, path in files.items() if path is None]
    empty = [table for table, path in files.items() if path is not None and path.stat().st_size == 0]
    return {"data_dir": data_dir, "files": files, "missing": missing, "empty": empty, "ready": not missing and not empty}
```

- [ ] **Step 4: Add `prepare_tpch_data`**

Implement:

- Return immediately if mode is `skip`.
- Return if data is ready and `force` is false.
- Fail if mode is `existing` and data is missing or empty.
- For `auto`, build dbgen if source dir exists and dbgen is missing.
- Run dbgen from a temporary generation directory.
- Move generated files into `data_dir`.
- Write `data-manifest.json`.

Use the existing `Runner` abstraction so tests can inspect commands.

- [ ] **Step 5: Call preparation before DDL**

In `_run_tpch_load`, call `prepare_tpch_data(...)` before `_tpch_data_preflight`.

- [ ] **Step 6: Run tests**

Run:

```bash
python -m unittest tests.test_tpch
```

Expected: PASS.

### Task 3: Upgrade TPC-H Check Behavior

**Files:**
- Modify: `automan_core/checks.py`
- Test: `tests/test_checks.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing check tests**

Add coverage for:

- `data_prepare.mode: auto` and missing data returns WARN/HINT plus dbgen readiness checks, not a fatal data-dir failure when dbgen can be built.
- `data_prepare.mode: existing` and missing data returns FAIL.
- Empty files return FAIL.

- [ ] **Step 2: Implement mode-aware TPC-H checks**

Modify `check_tpch_readiness` to use `tpch_data_status`.

For auto mode:

- OK if data ready.
- WARN if data missing and generator source exists or `dbgen_command` is available.
- FAIL if data missing and generator cannot be found or built.

For existing mode:

- FAIL on missing or empty.

For skip mode:

- HINT that data checks are skipped.

- [ ] **Step 3: Run check tests**

Run:

```bash
python -m unittest tests.test_checks tests.test_tpch
```

Expected: PASS.

### Task 4: Add TPC-H Playbook Entry Point

**Files:**
- Create: `tpch.yml`
- Create: `playbooks/tpch.yml`
- Create: `roles/tpch_prepare/tasks/main.yml`
- Create: `roles/tpch_run/tasks/main.yml`
- Test: `tests/test_cli_contract.py`

- [ ] **Step 1: Create root `tpch.yml` wrapper**

Implement the same stage parsing style as `ts.yml`, with valid stages `tpch-load` and `tpch-query`, then call:

```bash
exec ansible-playbook playbooks/tpch.yml "${args[@]}"
```

- [ ] **Step 2: Create playbook and roles**

`playbooks/tpch.yml` should run:

- `db_check`
- `tpch_prepare`
- `tpch_run`
- `collector`
- `archive`

`tpch_prepare` should validate and print manual parameter hints without doing data generation.

`tpch_run` should call:

```yaml
argv: "{{ [automan_python, '-m', 'automan_core', 'run', '-i', automan_config_file] + ((stage | default('') | length > 0) | ternary(['--stage', stage], [])) }}"
```

- [ ] **Step 3: Add wrapper contract test**

Add a test that reads `tpch.yml` and asserts it mentions `tpch-load`, `tpch-query`, and `playbooks/tpch.yml`.

- [ ] **Step 4: Run syntax checks locally where possible**

Run:

```bash
python -m unittest tests.test_cli_contract tests.test_tpch
```

Expected: PASS.

### Task 5: Make Reports Benchmark-Aware For TPC-H

**Files:**
- Modify: `automan_core/report.py`
- Test: `tests/test_report.py`
- Test: `tests/test_tpch.py`

- [ ] **Step 1: Add failing report test**

Create a minimal TPC-H job with one load result and one query result, call `generate_report`, and assert:

- Title contains `TPC-H`.
- Report contains `Scale Factor`, `Compress Threshold`, `Query Count`, and `Avg ms`.
- Report does not contain `Measured tpmC`.

- [ ] **Step 2: Implement benchmark dispatch in report generator**

When `job["benchmark"] == "tpch"`, render a TPC-H table with fields from `result.json`.

Keep existing TPC-C behavior unchanged.

- [ ] **Step 3: Run report tests**

Run:

```bash
python -m unittest tests.test_report tests.test_tpch
```

Expected: PASS.

### Task 6: End-To-End Remote Verification On 143

**Files:**
- Runtime only under `/root/automan` on `172.16.100.143`

- [ ] **Step 1: Sync modified files to 143**

Copy only changed files needed for TPC-H first-class support.

- [ ] **Step 2: Build or locate dbgen**

On 143, ensure `/root/automan/tools/tpch-dbgen/dbgen` exists. If not, copy or build from an available dbgen source. If only `/home/mxadmin/dbgen` exists on 29, copy the source or binary into 143 under `tools/tpch-dbgen`.

- [ ] **Step 3: Generate real SF10 data through Automan**

Run:

```bash
cd /root/automan
./configure -t tpch -c ym-mars3 -o runs/tpch-29-sf10-ct1200-real.yml
python3.8 - <<'PY'
from pathlib import Path
from automan_core.config import load_yaml, write_yaml
p = Path("runs/tpch-29-sf10-ct1200-real.yml")
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
./check.yml -i runs/tpch-29-sf10-ct1200-real.yml
./tpch.yml -i runs/tpch-29-sf10-ct1200-real.yml
```

- [ ] **Step 4: Verify real data and results**

Run:

```bash
find benchmarks/tpch/data/sf10 -maxdepth 1 -name '*.tbl' -printf '%f %s\n'
PGPASSWORD='YMatrix@123' psql -h 172.16.100.29 -p 5432 -U zhangchen -d tpch -Atc "select relname || ':' || n_live_tup from pg_stat_user_tables where relname in ('region','nation','supplier','customer','part','partsupp','orders','lineitem') order by relname;"
./automan list -t tpch --job <job_id>
./automan report --job <job_id>
```

Expected: eight non-empty files, non-zero table row counts, `tpch-load` success, `tpch-query` success, and a TPC-H report.
