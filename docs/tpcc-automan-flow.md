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
./configure -c pg -o automan.yml
./automan param -i automan.yml
./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan progress
./automan list
./report.yml -i automan.yml
```

Single-stage execution is available through the root wrapper:

```bash
./tpcc.yml -i automan.yml -s destroy
./tpcc.yml -i automan.yml -s load
./tpcc.yml -i automan.yml -s bench
```

The execution host must provide:

```text
Python 3.8
JDK
Ant
psql
Python packages from requirements.txt
```

BenchmarkSQL must be built on Linux before a job starts. From the source workspace, run:

```bash
python -m automan_core tools build-benchmarksql --host 172.16.100.143 --user root --remote-workdir /root/automan
```

The command runs `ant` on the Linux execution host and downloads the generated `tools/benchmarksql/dist/` back to the source workspace. Sync the project to `/root/automan` again after this step.

## Inventory Template

`automan` no longer asks for interactive database parameters. The preferred contract is a Pigsty-style inventory under `conf/`:

```bash
./configure -c pg -o automan.yml
./automan run -i automan.yml --plan-only
./automan run -i automan.yml
```

Current configure aliases:

```text
pg        PostgreSQL heap single node
ym-heap   YMatrix heap master only
ym-mars3  YMatrix mars3 master only
```

Multiple targets can be composed into one job:

```bash
./configure -c pg,ym-heap -o automan.yml
./configure -c pg,ym-heap,ym-mars3 -o automan.yml
```

Maintained inputs are `conf/tpcc/base.yml` and `conf/tpcc/targets/*.yml`.
Combination templates are not maintained as separate files.

Legacy task YAML remains available during migration:

```bash
./automan run --task configs/tasks/tpcc-postgresql-template.yaml --plan-only
```

Each inventory declares:

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

Every template field carries an inline YAML comment. Parameters such as `max_connections`, `shared_buffers`, `gpconfig_command`, and MARS3 options must be reviewed in `conf/tpcc/base.yml` and `conf/tpcc/targets/*.yml` before execution. `config_host` is optional and defaults to `db_host`; set it only when SSH must use a different host than the database connection.

## Database Profiles

Targets refer to database profiles by id:

```text
postgresql_heap_single_node
ymatrix_heap_master_only
ymatrix_mars3_master_only
```

## Manual Parameter Commands

`automan` and its Ansible playbooks do not modify database parameters, do not run `gpconfig`, do not edit `postgresql.conf`, and do not restart databases.

`./automan param -i automan.yml` prints the commands directly to the terminal. It does not create a shell script or a pre-review job. The command list is generated from `database_parameters` unless `manual_parameter_commands` is explicitly set in the inventory.

PostgreSQL commands include:

```text
ALTER SYSTEM SET <name> = <value>
show each changed parameter
```

YMatrix commands include:

```text
gpconfig -c <name> -v <value>
show each changed parameter
```

The user must execute and verify these commands manually before starting the actual pressure test. Reload or restart the database manually when a changed parameter requires it. The later `./tpcc.yml -i automan.yml` run creates the benchmark job.

## Execution Boundary

`automan` performs the TPC-C pressure test and, when collectors are enabled for the `database` role, uses SSH to check and collect database-host metrics. The SSH target is `config_host` when set, otherwise `db_host`. Current SSH checks require `config_user` and `config_password`; key and agent authentication are disabled.

`automan run` must be started on the configured execution host; the runner checks local host markers before doing destructive work.

## TPC-C Matrix

The inventory defines:

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

If a YMatrix mars3 target is declared, the inventory sets:

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

When `./tpcc.yml -s <stage>` is used, automan skips the normal full sequence and
runs only the selected BenchmarkSQL phase for each matrix entry:

```text
destroy -> runDatabaseDestroy.sh
load    -> runDatabaseBuild.sh
bench   -> runBenchmark.sh
```

`load` does not implicitly destroy existing objects. Run `-s destroy` explicitly
first when a clean load is required. `bench` assumes data has already been loaded.

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

## Running Progress

Show the current TPC-C job progress:

```bash
./automan progress
```

Watch progress with the default 5 second refresh:

```bash
./automan progress --watch
```

Watch progress with an explicit refresh interval:

```bash
./automan progress --watch 5
```

Inspect one job explicitly:

```bash
./automan progress --job <job_id>
```

`progress` auto-detects the single running job. If multiple jobs are running, it prints their job ids and requires `--job`. The output uses Pigsty-style status lines and maps TPC-C phases to short actions:

```text
schema_probe           action=probe
runDatabaseDestroy.sh  action=destroy
runDatabaseBuild.sh    action=load
runBenchmark.sh        action=test
report                 action=report
```

When the active phase is `runBenchmark.sh`, progress also prints elapsed time, expected time, remaining time, and percentage based on `run_mins`.

## Completed Results

Show completed run results from all jobs:

```bash
./automan list -t tpcc
```

Show completed run results for one job:

```bash
./automan list -t tpcc --job <job_id>
```

`list` defaults to `-t tpcc`; use `-t ts` for TS runs. It only prints runs that have finished successfully and already produced parseable BenchmarkSQL performance output. If a job has finished 100 terminals but 500 and 1000 terminals are still pending or failed, the 100-terminal result is still shown. Each row includes a stable `ID`, derived from the run id by removing the job prefix, such as `pg31-w100-c500`.

Delete one or more benchmark results by result ID:

```bash
./automan delete <id>
./automan delete <id> <id> -f
```

Without `-f`, delete requires typing `DELETE`. IDs are the stable hash `ID` values printed by `list`; full run IDs are also accepted. Deleting removes each selected `runs/<run_id>/` and referenced work directory, updates the owning job metadata, and removes the job directory when no runs remain.

List reads:

```text
runs/jobs/<job_id>/job.json
runs/jobs/<job_id>/resolved-plan.yaml
runs/<run_id>/benchmark/result/
```

It does not participate in execution.
