# TPC-H YMatrix Backend Design

## Goal

Switch Automan TPC-H from its internal DDL/load/query runner to the offline
`ymatrix-data/TPC-H` backend while preserving Automan's benchmark orchestration
model.

Automan remains responsible for:

- inventory composition and target profiles
- MARS3 parameter matrix expansion
- job and run directory layout
- stage entry points
- remote execution
- collector integration
- result normalization
- list and report output

The YMatrix TPC-H backend becomes responsible for:

- compiling and using TPC-H `dbgen` and `qgen`
- generating real TPC-H data
- creating the `tpch` schema and tables
- using the YMatrix/MatrixDB MARS3 DDL from the upstream project
- loading data through the upstream MatrixDB load path
- running generated TPC-H queries
- producing upstream logs and reports for Automan to archive and parse

## Source Version And Offline Requirement

Automan will vendor the upstream project under a repository-local tool path, for
example:

```text
tools/ymatrix-tpch/
```

The vendored source must come from:

```text
https://github.com/ymatrix-data/TPC-H
```

The first vendored version is fixed to commit:

```text
11bc7c50910f4bdb732a4615e7711beaa4914965
```

Automan must not require internet access during configure, check, load, query, or
remote verification. Any future upstream update is a deliberate repository
change, not an automatic runtime pull.

## Execution Location

Automan may run on a control host such as `172.16.100.143`, but the YMatrix TPC-H
backend must execute on the database master host for each target.

The database master host is not hardcoded. It is taken from the target database
connection:

```yaml
db_host: <database master ip>
```

For the current test case this happens to be `172.16.100.29`, but the
implementation must work for any target `db_host` supplied by the user.

Automan will use the target configuration credentials to stage and run the
backend on that host:

```yaml
config_user: <remote os user>
config_password: <remote os password>
config_workdir: <remote automan workdir>
```

The remote backend work directory should be isolated per run under
`config_workdir`, for example:

```text
<config_workdir>/runs/<run_id>/ymatrix-tpch
```

This keeps upstream-generated files, logs, and reports tied to the Automan run
that created them.

## Stage Mapping

Automan keeps the existing TPC-H stages:

```text
tpch-load
tpch-query
```

The backend maps these stages to upstream switches.

`tpch-load` runs the upstream preparation path:

```text
RUN_COMPILE_TPCH=true
RUN_GEN_DATA=true
RUN_INIT=true
RUN_DDL=true
RUN_LOAD=true
RUN_SQL=false
RUN_SINGLE_USER_REPORT=false
RUN_MULTI_USER=false
RUN_MULTI_USER_REPORT=false
```

`tpch-query` runs the upstream query and single-user report path:

```text
RUN_COMPILE_TPCH=false
RUN_GEN_DATA=false
RUN_INIT=false
RUN_DDL=false
RUN_LOAD=false
RUN_SQL=true
RUN_SINGLE_USER_REPORT=true
RUN_MULTI_USER=false
RUN_MULTI_USER_REPORT=false
```

Multi-user support remains a future extension unless explicitly configured in a
later design. The first implementation keeps `query_streams` compatible with the
current single-user matrix by mapping one stream to the upstream single-user
query path.

## Configuration Model

The current TPC-H matrix remains valid:

```yaml
compress_threshold: [1200, 4096, 8192, 32768]
tpch:
  scale_factors: [10]
  query_streams: [1]
```

TPC-H gains a backend configuration block. The default backend is the YMatrix
upstream backend:

```yaml
tpch:
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
```

The implementation may keep legacy `data_prepare` fields temporarily for
compatibility, but normal TPC-H execution must use the new backend.

## MARS3 Parameter Matrix

Automan keeps ownership of the MARS3 parameter matrix. For a MARS3 target, each
`compress_threshold` value creates a separate Automan run.

For each run, Automan injects the matrix value into the upstream storage
variables:

```shell
SMALL_STORAGE="USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=<value>)"
MEDIUM_STORAGE="USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=<value>)"
LARGE_STORAGE="USING mars3 with (compresstype=zstd, compresslevel=2, compress_threshold=<value>)"
```

This preserves the current Automan behavior of testing MARS3 settings across a
matrix while using the upstream DDL files. The upstream MARS3-specific DDL,
including partitioning and `mars3_brin` indexes, remains active.

For non-MARS3 targets, `compress_threshold` is not expanded, matching the current
Automan matrix behavior.

## Load Method

For MatrixDB/YMatrix targets, the backend follows the upstream repository's load
method.

The default is:

```shell
LOAD_DATA_TYPE="mxgate"
```

This is why the backend runs on the database master host instead of the Automan
control host. The upstream MatrixDB load path inspects cluster metadata, stages
scripts to primary segment hosts, and runs load helpers in the expected database
environment.

`gpfdist` can remain available as an explicit backend option, but Automan should
not silently replace the upstream MatrixDB default.

## Schema And Queries

The backend uses the upstream schema name:

```text
tpch
```

Automan should stop rendering its internal `public` schema TPC-H SQL for normal
YMatrix backend runs.

Queries are generated by upstream `qgen` through the upstream scripts. Automan
archives generated SQL files and logs, then parses timing or status information
back into the standard Automan result shape.

## Remote Environment

Automan passes database connection settings to the remote backend environment:

```shell
PGHOST=<db_host>
PGPORT=<db_port>
PGDATABASE=<db_name>
PGUSER=<db_user>
PGPASSWORD=<db_password>
```

It also sets upstream-required settings such as:

```shell
DATABASE_TYPE="matrixdb"
ACCESS_METHOD="mars3"
LOAD_DATA_TYPE="mxgate"
GEN_DATA_SCALE=<scale_factor>
```

If `GREENPLUM_PATH` is not configured, Automan may allow the upstream script to
discover it. If the target environment requires an explicit path, a backend
option should expose it without hardcoding a product installation directory.

## Run Artifacts

Each Automan run should capture:

- rendered upstream variable file
- upstream command stdout and stderr
- generated SQL
- upstream load logs
- upstream query logs
- upstream report files
- normalized `result.json`
- normalized status files
- collector artifacts

Artifacts copied back from the database master are stored under the existing
Automan run directory:

```text
runs/<run_id>/
```

## Result Normalization

Automan reports still use the existing benchmark-aware reporting surface.

For `tpch-load`, the normalized result should include at least:

- status
- scale factor
- compress threshold
- load elapsed seconds
- loaded table count or loaded tuple count when available
- table data size when available
- backend type
- remote backend directory
- upstream log/report locations

For `tpch-query`, the normalized result should include at least:

- status
- scale factor
- compress threshold
- query count
- elapsed seconds
- avg/p50/p95/p99 latency when parseable
- error count
- qphh or queries per hour when parseable
- backend type
- remote backend directory
- upstream log/report locations

If an upstream report format cannot provide one of these fields reliably, Automan
records the field as `null` and keeps the upstream artifacts available for manual
inspection.

## Check Behavior

`./check.yml -i <inventory>` must validate the YMatrix backend prerequisites:

- vendored source exists locally
- remote database master is reachable through `config_user`
- remote `config_workdir` is writable
- database connection works from the remote master environment
- `psql`, `make`, compiler toolchain, and shell utilities needed by upstream are
  present
- MatrixDB/YMatrix cluster metadata can be queried for MatrixDB load mode
- requested `LOAD_DATA_TYPE` is supported
- enough disk space is plausibly available for the requested scale factor

Checks should fail early when a missing prerequisite would make the backend
unable to run. They should not require pre-existing `.tbl` files when
`RUN_GEN_DATA=true`.

## Error Handling

Automan must surface backend failures with the upstream stage and log path.

Examples:

- compile failure: mark `tpch-load` failed and point to compile logs
- data generation failure: mark `tpch-load` failed and point to generation logs
- DDL failure: mark `tpch-load` failed and point to DDL logs
- load failure: mark `tpch-load` failed and point to load logs
- query failure: mark `tpch-query` failed and point to query logs
- artifact collection failure: preserve remote path and report partial artifacts

The job should not be marked successful if any matrix run fails.

## Compatibility And Migration

The existing internal TPC-H runner can remain in code temporarily only as a
legacy fallback or unit-test utility, but it is no longer the default backend for
configured TPC-H workloads.

The default `conf/tpch/base.yml` should select the YMatrix backend so the normal
user flow uses the upstream implementation:

```bash
./configure -t tpch -c ym-mars3 -o automan.yml
./check.yml -i automan.yml
./tpch.yml -i automan.yml
```

## Verification Target

The first real verification uses the user's current scenario:

```yaml
config_password: mxadmin
config_user: mxadmin
config_workdir: /home/mxadmin/automan
db_host: 172.16.100.29
db_name: tpch
db_password: YMatrix@123
db_port: 5432
db_type: ymatrix
db_user: zhangchen
scale_factors: [10]
compress_threshold: [1200]
```

Verification must prove:

- the vendored backend is used, not the old internal TPC-H DDL/load/query path
- the backend runs on the target `db_host`
- generated data is real and non-empty
- upstream DDL creates the `tpch` schema
- MARS3 storage includes `compress_threshold=1200`
- load uses the upstream MatrixDB path
- query stage runs through upstream generated SQL
- Automan stores normalized results and upstream artifacts
- `automan list -t tpch` and `automan report --job <job_id>` work for the job

## Tests

Unit and contract coverage should include:

- backend config loading defaults to `ymatrix-tpch`
- run spec expansion preserves MARS3 `compress_threshold` matrix
- remote backend directory is derived from each run id
- target `db_host` is used as backend execution host
- rendered upstream variables include scale factor, schema, load type, and MARS3
  storage settings
- `tpch-load` maps to compile/gen/init/ddl/load only
- `tpch-query` maps to query/report only
- old internal schema/query files are not used by the default backend
- result normalization handles successful and failed upstream logs
- report generation accepts normalized YMatrix backend results

Remote verification on the current environment should run after local tests pass.
