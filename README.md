# AUTOMAN

Pigsty-style benchmark automation for PostgreSQL and YMatrix.

Automan is a config-driven operations toolkit for database benchmark runs. The current production target is TPC-C through BenchmarkSQL, with manual-only database parameter changes, preflight collector checks, CPU/memory/IO and `perf record` artifacts, repeatable run archives, progress tracking, and objective report output.

## Quick Start

Run from the Linux execution host:

```bash
cd /root/automan

./configure -c tpcc/pg -o automan.yml
./bin/validate -i automan.yml

./automan param -i automan.yml
# Review and run runs/campaigns/<campaign_id>/manual-parameter-commands.sh manually if needed.

./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan progress
./report.yml -i automan.yml
```

Source edits are made in this repository. Real benchmark execution belongs on:

```text
172.16.100.143:/root/automan
```

## Operating Model

Automan follows the same practical shape as Pigsty:

- `conf/` contains benchmark configuration templates.
- Root scripts and playbooks are direct operation entry points.
- Python handles validation, planning, rendering, progress, and reports.
- Ansible handles remote command execution and file operations.
- Database parameter changes are generated for manual execution and are never applied automatically.

## TPC-C Flow

Every run follows the same sequence:

```text
validate config
generate campaign plan
generate manual parameter commands
check execution environment
check collector tools and perf record permission
prepare BenchmarkSQL workspace
schema probe
destroy existing bmsql_* objects when needed
build database
run benchmark while collecting system metrics and perf record artifacts
archive logs and status
generate report
```

For the first run of each target, `runDatabaseDestroy.sh` is skipped only when schema probe proves that no `bmsql_*` objects exist. Later runs always execute destroy, build, and benchmark.

## Important Rules

- Do not run long benchmarks from the source workspace.
- Do not let automan modify database parameters automatically.
- Do not start benchmark runs until `./check.yml -i automan.yml` confirms database connectivity and all configured collectors are usable.
- Keep passwords in local configs only; generated plans redact secrets.
- Treat BenchmarkSQL output containing `FATAL`, `ERROR`, `Exception`, `Failed to`, or authentication failure as a failed phase even if the shell script returns `0`.

## Layout

```text
conf/                 user-facing benchmark templates
playbooks/            Ansible playbook implementations
roles/                Ansible roles for checks, prepare, run, archive, report
automan_core/         Python control plane
benchmarks/           benchmark-specific SQL and DDL profiles
tools/benchmarksql/   BenchmarkSQL source and dist
runs/                 generated campaign and run artifacts
```
