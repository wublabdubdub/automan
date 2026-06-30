# AP and TPC-H Scenario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class `ap` and `tpch` benchmark types to Automan using the existing `configure -t`, `run --stage`, `plan`, `validate`, and `list` flow.

**Architecture:** Keep AP and TPC-H beside the existing TPC-C and TS paths, with small benchmark-specific modules for config loading, validation, run-spec generation, and job file writing. `task_runner.py` remains the orchestration boundary, while `list_results.py` renders AP/TPC-H rows from published `result.json` files the same way it already handles TS rows.

**Tech Stack:** Python stdlib, YAML templates under `conf/`, existing Automan dataclasses, unittest.

**Status:** Implemented in this session and extended to real execution. Focused verification used `python -m unittest` because `pytest` is not installed on the local machine.

**Execution Completion:** AP now executes SQL from `benchmarks/ap/queries/<query_set>`, records latency/rows/errors/table size, and writes `result.json`. TPC-H now executes `tpch-load` with `pg`, `ym-heap`, and `ym-mars3` DDL, imports `.tbl` or `.tbl.gz` data, analyzes tables, records table size, and executes 22 SQL files for `tpch-query` with latency/QphH metrics.

---

### Task 1: Add AP/TPC-H Configuration Templates

**Files:**
- Create: `conf/ap/base.yml`
- Create: `conf/ap/targets/ym-mars3.yml`
- Create: `conf/tpch/base.yml`
- Create: `conf/tpch/targets/pg.yml`
- Create: `conf/tpch/targets/ym-heap.yml`
- Create: `conf/tpch/targets/ym-mars3.yml`
- Test: `tests/test_ap.py`
- Test: `tests/test_tpch.py`

- [x] **Step 1: Write configure tests**

Add tests that call:

```python
cli.configure_main()
```

with:

```python
["configure", "-t", "ap", "-c", "ym-mars3", "-o", "automan.yml"]
["configure", "-t", "tpch", "-c", "pg,ym-heap,ym-mars3", "-o", "automan.yml"]
```

Expected AP output contains `benchmark: ap`, `ap_stages: [ap-query]`, and `compress_threshold: [1200, 4096, 8192, 32768]`.

Expected TPC-H output contains `benchmark: tpch`, `tpch_stages: [tpch-load, tpch-query]`, `tpch_scale_factors: [1]`, and three target groups.

- [x] **Step 2: Add templates**

AP base variables:

```yaml
benchmark: ap
compress_threshold: [1200, 4096, 8192, 32768]
ap_stages: [ap-query]
ap_query:
  source_table: iot_vehicle_raw
  rounds: [3]
  warmup_rounds: [1]
  timeout_seconds: 7200
  query_set: vehicle_ap_basic
```

TPC-H base variables:

```yaml
benchmark: tpch
compress_threshold: [1200, 4096, 8192, 32768]
tpch_stages: [tpch-load, tpch-query]
tpch:
  scale_factors: [1]
  query_streams: [1]
  query_set: standard
```

TPC-H target variables include `tpch_ddl_profile: pg`, `ym-heap`, or `ym-mars3`.

- [x] **Step 3: Extend CLI type choices**

Change:

```python
CONFIG_TYPES = ("tpcc", "ts")
```

to:

```python
CONFIG_TYPES = ("tpcc", "ts", "ap", "tpch")
```

Keep `CONFIG_ALIASES` shared so `-c pg,ym-heap,ym-mars3` works for TPC-H.

- [x] **Step 4: Run configure tests**

Run:

```bash
python -m pytest tests/test_ap.py tests/test_tpch.py -q
```

Expected: new configure tests pass.

### Task 2: Add AP Run Planning

**Files:**
- Modify: `automan_core/models.py`
- Create: `automan_core/ap.py`
- Modify: `automan_core/task_runner.py`
- Test: `tests/test_ap.py`

- [x] **Step 1: Add AP dataclasses**

Add:

```python
@dataclass(frozen=True)
class ApQueryConfig:
    source_table: str
    rounds: list[int]
    warmup_rounds: list[int]
    timeout_seconds: int
    query_set: str


@dataclass(frozen=True)
class ApConfig:
    stages: list[str]
    compress_threshold: list[int]
    query: ApQueryConfig


@dataclass(frozen=True)
class ApRunSpec:
    run_id: str
    target_id: str
    stage: str
    compress_threshold: int
    source_table: str
    query_set: str
    rounds: int
    warmup_rounds: int
    run_dir: Path
    benchmark_dir: Path
    database_dir: Path
    logs_dir: Path
    collector_dir: Path
```

- [x] **Step 2: Implement `automan_core/ap.py`**

Functions:

```python
AP_STAGES = ("ap-query",)
def new_ap_job_id() -> str: ...
def load_ap_config(vars_: dict[str, Any]) -> ApConfig: ...
def validate_ap_config(config: ApConfig, targets: list[Target]) -> list[Any]: ...
def build_ap_run_specs(root: Path, job_id: str, targets: list[Target], config: ApConfig, stage: str | None) -> list[ApRunSpec]: ...
def write_ap_job_files(root: Path, job_id: str, targets: list[Target], config: ApConfig, runs: list[ApRunSpec], collectors: dict[str, Any] | None, stage: str | None) -> Path: ...
```

Run order is threshold-major, then target, then query-round combination.

- [x] **Step 3: Wire AP into task runner**

`TaskDefinition` gets `ap_config: ApConfig | None`.

Inventory loading calls `load_ap_config(all_vars)` when `benchmark == "ap"`.

Validation calls `validate_ap_config`.

`run_task_job(... stage="ap-query")` writes AP job files.

- [x] **Step 4: Run AP tests**

Run:

```bash
python -m pytest tests/test_ap.py -q
```

Expected: AP validation and plan tests pass.

### Task 3: Add TPC-H Run Planning

**Files:**
- Modify: `automan_core/models.py`
- Create: `automan_core/tpch.py`
- Modify: `automan_core/task_runner.py`
- Test: `tests/test_tpch.py`

- [x] **Step 1: Add TPC-H dataclasses**

Add:

```python
@dataclass(frozen=True)
class TpchConfig:
    stages: list[str]
    compress_threshold: list[int]
    scale_factors: list[int]
    query_streams: list[int]
    query_set: str


@dataclass(frozen=True)
class TpchRunSpec:
    run_id: str
    target_id: str
    stage: str
    ddl_profile: str
    compress_threshold: int | None
    scale_factor: int
    query_streams: int
    run_dir: Path
    benchmark_dir: Path
    database_dir: Path
    logs_dir: Path
    collector_dir: Path
```

- [x] **Step 2: Implement `automan_core/tpch.py`**

Functions:

```python
TPCH_STAGES = ("tpch-load", "tpch-query")
def new_tpch_job_id() -> str: ...
def load_tpch_config(vars_: dict[str, Any]) -> TpchConfig: ...
def validate_tpch_config(config: TpchConfig, targets: list[Target]) -> list[Any]: ...
def tpch_ddl_profile(target: Target) -> str: ...
def build_tpch_run_specs(root: Path, job_id: str, targets: list[Target], config: TpchConfig, stage: str | None) -> list[TpchRunSpec]: ...
def write_tpch_job_files(root: Path, job_id: str, targets: list[Target], config: TpchConfig, runs: list[TpchRunSpec], collectors: dict[str, Any] | None, stage: str | None) -> Path: ...
```

`pg` and `ym-heap` use `compress_threshold=None`; `ym-mars3` generates one run per `compress_threshold`.

- [x] **Step 3: Wire TPC-H into task runner**

`TaskDefinition` gets `tpch_config: TpchConfig | None`.

Inventory loading calls `load_tpch_config(all_vars)` when `benchmark == "tpch"`.

Validation calls `validate_tpch_config`.

`run_task_job(... stage="tpch-load" | "tpch-query")` writes TPC-H job files.

- [x] **Step 4: Run TPC-H tests**

Run:

```bash
python -m pytest tests/test_tpch.py -q
```

Expected: TPC-H validation and plan tests pass.

### Task 4: Extend Result Listing

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_ap.py`
- Test: `tests/test_tpch.py`
- Test: `tests/test_cli_contract.py`

- [x] **Step 1: Add list type coverage**

Parser accepts:

```python
["list", "-t", "ap"]
["list", "-t", "tpch"]
```

and `run --stage` accepts:

```python
"ap-query", "tpch-load", "tpch-query"
```

- [x] **Step 2: Add AP row rendering**

AP rows include:

```python
"benchmark", "id", "job", "run", "stage", "target", "db_host",
"source_table", "compress_threshold", "table_data_size",
"rounds", "query_count", "avg_ms", "p50_ms", "p95_ms", "p99_ms",
"rows_returned", "errors", "session_start", "session_end", "result_dir"
```

- [x] **Step 3: Add TPC-H row rendering**

TPC-H rows include:

```python
"benchmark", "id", "job", "run", "stage", "target", "db_host",
"ddl_profile", "compress_threshold", "scale_factor", "query_streams",
"table_data_size", "elapsed_seconds", "qphh",
"query_count", "avg_ms", "p95_ms", "errors", "session_start",
"session_end", "result_dir"
```

- [x] **Step 4: Run list tests**

Run:

```bash
python -m pytest tests/test_ap.py tests/test_tpch.py tests/test_cli_contract.py tests/test_list_results.py -q
```

Expected: list stays defaulted to TPC-C, while `-t ap` and `-t tpch` show the new benchmark-specific columns.

### Task 5: Full Verification and Sync

**Files:**
- Modify: `agent.md` only if a new durable rule is discovered.

- [x] **Step 1: Run focused tests**

Run:

```bash
python -m pytest tests/test_ap.py tests/test_tpch.py tests/test_ts.py tests/test_cli_contract.py tests/test_list_results.py -q
```

Expected: pass.

- [x] **Step 2: Sync to 143**

Sync changed files to:

```bash
root@172.16.100.143:/root/automan
```

Expected: remote copy succeeds.

- [x] **Step 3: Report final state**

Summarize the files changed, tests run, and any execution pieces intentionally left as plan/list scaffolding.
