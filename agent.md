# Agent Memory

## Execution Location

The current workspace is the source directory only. It is used for editing project code, configs, docs, templates, and local assets.

Actual benchmark execution must happen on the remote execution host:

```text
host: 172.16.100.143
user: root
password: root
remote_path: /root/automan
```

Before running real tests, upload or sync the project from the current source directory to:

```text
root@172.16.100.143:/root/automan
```

All BenchmarkSQL builds, generated work files, benchmark execution, metrics collection, perf collection, and final run artifacts should be produced from the remote path unless explicitly stated otherwise.

## Current Operating Contract

Automan follows a Pigsty-style workflow:

```bash
./configure -t tpcc -c tpcc/pg -o automan.yml
./automan param -i automan.yml
./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan progress
./report.yml -i automan.yml
```

Users no longer need to run `./bin/validate -i automan.yml` manually. `check.yml` runs validation automatically before remote readiness checks.

Database parameter changes are manual-only. Automan can render `manual-parameter-commands.sh`, but it must not edit `postgresql.conf`, run `gpconfig`, restart PostgreSQL, or restart YMatrix automatically.

Legacy task YAML is still supported with `./automan run --task <task.yaml>` during migration, but new work should use `conf/<test-type>/*.yml`.

## Test Type Convention

Automan test type selection uses `configure -t` for all current and future benchmark scenarios.

Examples:

```bash
./configure -t ts -c ym-mars3 -o automan.yml
./configure -t tpcc -c pg,ym-heap -o automan.yml
./configure -t tpch -c ym-mars3 -o automan.yml
```

Use this convention for new scenario development. Known type names include:

```text
ts    time-series scenarios, including time-series write, time-series query, and point query
tpcc  TPC-C scenarios
tpch  TPC-H scenarios
```

## Sync Requirement

After every code, config, template, or documentation modification, sync the changed project to:

```text
root@172.16.100.143:/root/automan
```

This sync is required unless the user explicitly says not to sync changes to 143.

## Dev Documentation Convention

Design and development notes under `dev/` must use Chinese file names and Chinese content.
