# automan TPC-C flow

This document is the implementation contract for the current TPC-C-only phase.

## Execution Host

The local workspace is the source directory. Real execution happens on:

```text
host: 172.16.100.143
user: root
remote_path: /root/automan
```

Run from the remote path:

```bash
cd /root/automan
./automan run
```

## Interactive Selection

`automan run` uses hierarchical selection:

```text
database type -> storage engine -> test mode
```

Single-option steps are skipped and applied as defaults.

Current options:

```text
PostgreSQL:
  storage_engine: heap
  test_mode: single_node

YMatrix:
  storage_engine: heap | mars3
  test_mode: master_only
```

The selected combinations resolve to database profiles:

```text
postgresql_heap_single_node
ymatrix_heap_master_only
ymatrix_mars3_master_only
```

## Server And Database Input

For each target, the user enters:

```text
SSH host
SSH port
SSH user
SSH password
remote workdir
database host
database port
database name
database user
database password
```

PostgreSQL also asks for:

```text
postgresql.conf path
PostgreSQL restart command
```

YMatrix also asks for:

```text
gpconfig command path
YMatrix restart command
```

The default YMatrix restart command is:

```bash
mxstop -afr
```

## Host Probe And Parameter Application

After SSH input, `automan` probes CPU and memory through SSH.

It recommends database parameters. After user confirmation:

PostgreSQL:

```text
1. Backup postgresql.conf.
2. Write an automan-managed settings block.
3. Run the user-provided PostgreSQL restart command.
```

YMatrix:

```text
1. Run gpconfig -c <name> -v <value> for each parameter.
2. Run mxstop -afr.
```

The user can also choose not to apply parameters.

## TPC-C Matrix

The user enters:

```text
warehouses: multi-value
terminals: multi-value
runMins: single value
loadWorkers: single value
```

`warehouses` and `terminals` form the run matrix.

Example:

```text
3 targets * 2 warehouses * 3 terminals = 18 runs
```

`loadWorkers` is intentionally single-value in this phase because it affects data loading rather than measured transaction concurrency.

## MARS3 DDL Options

If YMatrix mars3 is selected, the user confirms:

```text
prefer_load_mode [single]
rowstore_size [64]
compresstype [zstd]
compresslevel [1]
compress_threshold [1200]
```

The default MARS3 table suffix is:

```sql
USING MARS3
WITH(
  mars3options='prefer_load_mode=single,rowstore_size=64',
  compresstype=zstd,
  compresslevel=1,
  compress_threshold=1200
)
DISTRIBUTED MASTERONLY;
```

## DDL Profiles

PostgreSQL heap single node:

```text
BenchmarkSQL default PostgreSQL SQL.
```

YMatrix heap master only:

```text
BenchmarkSQL default PostgreSQL SQL.
```

YMatrix mars3 master only:

```text
tableCreates.sql uses MARS3 and DISTRIBUTED MASTERONLY.
Other SQL files stay aligned with BenchmarkSQL defaults.
```

## Per-Run Sequence

Every TPC-C run must execute the full BenchmarkSQL sequence:

```bash
runDatabaseDestroy.sh
runDatabaseBuild.sh
runBenchmark.sh
```

Skipping destroy/build is not allowed.

Each run gets an isolated BenchmarkSQL working copy:

```text
work/tpcc/benchmarksql/<run_id>/benchmarksql/
```

The selected DDL profile is installed into that per-run copy. This prevents PostgreSQL heap, YMatrix heap, and YMatrix mars3 runs from overwriting each other's `sql.common` or `sql.postgres` directories.

The generated properties file lives at:

```text
work/tpcc/benchmarksql/<run_id>/tpcc.properties
```

## Scheduling

Targets on different hosts run in parallel.

Targets on the same host run serially in the user-selected order.

## Progress

Show the latest campaign:

```bash
./automan progress
```

Watch progress:

```bash
./automan progress --watch --interval 5
```

Inspect a specific campaign:

```bash
./automan progress --campaign <campaign_id>
```

Progress reads:

```text
runs/campaigns/<campaign_id>/progress.json
```

It does not participate in execution.
