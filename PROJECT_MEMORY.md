# Project Memory

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

Automan now follows a Pigsty-style workflow:

```bash
./configure -c tpcc/pg -o automan.yml
./bin/validate -i automan.yml
./automan param -i automan.yml
./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan progress
./report.yml -i automan.yml
```

Database parameter changes are manual-only. Automan can render `manual-parameter-commands.sh`, but it must not edit `postgresql.conf`, run `gpconfig`, restart PostgreSQL, or restart YMatrix automatically.

Legacy task YAML is still supported with `./automan run --task <task.yaml>` during migration, but new work should use `conf/tpcc/*.yml`.
