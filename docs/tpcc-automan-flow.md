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
./automan run --task configs/tasks/tpcc-postgresql-template.yaml
```

The execution host must provide:

```text
Python 3.8
JDK
Ant
psql
Python packages from requirements.txt
```

BenchmarkSQL must be built on Linux before a campaign starts. From the source workspace, run:

```bash
python -m automan_core tools build-benchmarksql --host 172.16.100.143 --user root --remote-workdir /root/automan
```

The command runs `ant` on the Linux execution host and downloads the generated `tools/benchmarksql/dist/` back to the source workspace. Sync the project to `/root/automan` again after this step.

## Task Template

`automan run` no longer asks for interactive database parameters. Every campaign must be started from a task YAML:

```bash
./automan run --task <task.yaml>
```

Current templates:

```text
configs/tasks/tpcc-postgresql-template.yaml
configs/tasks/tpcc-ymatrix-template.yaml
```

Each task YAML declares:

```text
execution host
TPC-C matrix
one or more targets
database profile
database connection
database parameters for manual review
optional manual parameter commands
MARS3 DDL options when needed
```

Every template field carries an inline YAML comment. Parameters such as `max_connections`, `shared_buffers`, `gpconfig_command`, `restart_command`, and MARS3 options must be reviewed in the template before execution.

## Database Profiles

Targets refer to database profiles by id:

```text
postgresql_heap_single_node
ymatrix_heap_master_only
ymatrix_mars3_master_only
```

## Manual Parameter Commands

`automan` does not modify database parameters, does not run `gpconfig`, does not edit `postgresql.conf`, and does not restart databases.

For each campaign it writes:

```text
runs/campaigns/<campaign_id>/manual-parameter-commands.sh
```

This file is generated from `database_parameters` unless `manual_parameter_commands` is explicitly set in the task YAML.

PostgreSQL commands include:

```text
backup postgresql.conf
write automan managed settings block
run the declared restart command
show each changed parameter
```

YMatrix commands include:

```text
gpconfig -c <name> -v <value>
the declared restart command, usually mxstop -afr
show each changed parameter
```

The user must execute and verify these commands manually before starting the actual pressure test, or run `--plan-only` first, apply the commands, and then run the task normally.

## Execution Boundary

`automan` only performs the actual TPC-C pressure test. It uses the database connection in the task YAML to run BenchmarkSQL and schema probes. It does not SSH to the config host during benchmark execution.

`automan run` must be started on the configured execution host; the runner checks local host markers before doing destructive work.

## TPC-C Matrix

The task YAML defines:

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

If a YMatrix mars3 target is declared, the task YAML sets:

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

The first run for each target is marked with:

```text
skip_destroy: true
```

Before executing it, `automan` probes the target database for existing `bmsql_*` objects with `psql`.

If no TPC-C objects exist, it executes:

```bash
runDatabaseBuild.sh
runBenchmark.sh
```

If TPC-C objects already exist, or for later runs of the same target, it executes the full BenchmarkSQL sequence:

```bash
runDatabaseDestroy.sh
runDatabaseBuild.sh
runBenchmark.sh
```

Skipping build is never allowed. Destroy is skipped only when the first-run schema probe confirms there are no existing TPC-C objects.

All maintained TPC-C `tableDrops.sql` profiles use `drop ... if exists`, so destroy is idempotent against partially cleaned schemas.

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

Targets on different database hosts run in parallel.

Targets on the same database host run serially in the user-selected order.

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
