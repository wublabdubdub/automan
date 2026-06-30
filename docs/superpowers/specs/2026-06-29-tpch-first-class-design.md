# TPC-H First-Class Benchmark Design

## Goal

Make TPC-H a first-class Automan benchmark with the same operational shape as TPC-C: configure, check, prepare data, load, query, collect artifacts, list results, and generate an objective report from one supported workflow.

## Scope

This design covers the `tpch` benchmark type only. It does not change TPC-C, TS, or AP behavior except where shared report/list helpers need benchmark-aware formatting.

The target user flow is:

```bash
./configure -t tpch -c ym-mars3 -o automan.yml
./check.yml -i automan.yml
./tpch.yml -i automan.yml
./automan list -t tpch --job <job_id>
./automan report --job <job_id>
```

Stage mode is supported:

```bash
./tpch.yml -i automan.yml -s tpch-load
./tpch.yml -i automan.yml -s tpch-query
```

## Configuration

TPC-H keeps the existing inventory style under `conf/tpch/`.

The base config gains explicit data preparation settings:

```yaml
tpch:
  scale_factors: [10]
  query_streams: [1]
  query_set: standard
  data_dir: benchmarks/tpch/data/sf{scale_factor}
  schema_dir: benchmarks/tpch/schema
  query_dir: benchmarks/tpch/queries
  data_prepare:
    mode: auto
    generator: dbgen
    source_dir: tools/tpch-dbgen
    build_command: make
    dbgen_command: ./dbgen
    force: false
```

`mode` supports:

- `auto`: build or use dbgen and generate missing real `.tbl` files before `tpch-load`.
- `existing`: require all `.tbl` or `.tbl.gz` files to already exist.
- `skip`: do not check or generate data. This is intended only for query-only workflows against already loaded tables.

The default should be `auto` for TPC-H templates so a configured SF run has a real data path.

## Data Preparation

Automan will add a TPC-H data preparation unit responsible for:

- Checking whether all eight TPC-H `.tbl` files exist for each scale factor.
- Verifying existing files are non-empty.
- Building dbgen when needed.
- Running dbgen once per configured scale factor when data is missing.
- Moving or generating files into `tpch.data_dir`.
- Writing a manifest beside the generated data, including scale factor, command, start/end time, file sizes, and row counts when cheap to collect.

The canonical table set is:

```text
region, nation, supplier, customer, part, partsupp, orders, lineitem
```

Generation must happen on the execution host, because `tpch-load` uses local `psql \copy` from the execution host. For `172.16.100.143:/root/automan`, generated files therefore live under `/root/automan/benchmarks/tpch/data/sf10`.

Automan should not create empty placeholder files. Empty files are a failure unless the scale factor is explicitly a tiny test fixture in unit tests.

## Check Behavior

`./check.yml -i automan.yml` remains the preflight gate.

For TPC-H:

- It validates schema and query files.
- It validates database connectivity.
- It validates collector tools.
- For `data_prepare.mode: existing`, it fails if data is missing or empty.
- For `data_prepare.mode: auto`, it checks generator readiness and disk space, and reports missing data as actionable but not fatal if generation can proceed.
- For `data_prepare.mode: skip`, it does not require data files.

The check output must clearly state whether data will be generated automatically.

## Run Behavior

`tpch-load` flow:

1. Prepare run directories and job metadata.
2. If `data_prepare.mode: auto`, generate missing data for the run scale factor.
3. Render and execute target-specific DDL.
4. Transform `.tbl` trailing delimiters into `.copy` work files.
5. Load all eight tables with `psql \copy`.
6. Run `analyze`.
7. Record table size and row counts.
8. Stop collectors and archive artifacts.

`tpch-query` flow:

1. Read the configured query set.
2. Execute the configured number of complete query streams.
3. Write per-query outputs and command logs.
4. Record query count, latency summary, rows returned, errors, qphh-compatible internal throughput, table size, and row counts.

## CLI And Playbooks

Add root and playbook entry points matching TPC-C style:

- `tpch.yml`
- `playbooks/tpch.yml`
- `roles/tpch_prepare/tasks/main.yml`
- `roles/tpch_run/tasks/main.yml`

`tpch.yml` accepts `-s/--stage` with:

```text
tpch-load, tpch-query
```

The role implementation can call:

```bash
python3.8 -m automan_core run -i automan.yml --stage tpch-load
```

or omit `--stage` for the full flow.

## Reporting

`automan report` becomes benchmark-aware.

For TPC-H jobs, report title and tables should use TPC-H fields, not TPC-C fields:

- SF
- query streams
- run mins
- DDL profile
- compress threshold
- load elapsed seconds
- query elapsed seconds
- query count
- avg/p50/p95/p99 latency
- errors
- table data size
- row counts
- collector artifacts
- failure snippets

Existing TPC-C report output remains unchanged for TPC-C jobs.

## Error Handling

TPC-H failures should be explicit and early:

- Missing dbgen in `existing` mode is irrelevant.
- Missing dbgen in `auto` mode is a check failure unless source can be built.
- Missing or empty `.tbl` files fail `tpch-load` before DDL starts unless `auto` can generate them.
- Generation failure records stdout/stderr under the run logs.
- `tpch-query` fails if no query succeeds or any query errors.

## Testing

Unit coverage should include:

- Config loading for `data_prepare`.
- `check` behavior for `auto`, `existing`, and missing data.
- Run spec/job plan includes data preparation metadata.
- Data preparation skips complete non-empty data.
- Data preparation invokes dbgen for missing data.
- Empty `.tbl` files fail.
- `tpch.yml -s` argument validation.
- TPC-H report uses TPC-H title and fields.

Remote verification on `172.16.100.143` should run:

```bash
./configure -t tpch -c ym-mars3 -o runs/tpch-29-sf10-ct1200-real.yml
./check.yml -i runs/tpch-29-sf10-ct1200-real.yml
./tpch.yml -i runs/tpch-29-sf10-ct1200-real.yml
./automan list -t tpch --job <job_id>
./automan report --job <job_id>
```

The real SF10 run must use non-empty generated data and must not rely on placeholder files.
