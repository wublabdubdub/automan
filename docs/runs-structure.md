# runs directory structure

Each test execution creates one immutable run directory. The run directory is the source of truth for reproduction, comparison, and report generation.

## Naming

When `run.run_id` is `auto`, the controller generates:

```text
<YYYYMMDD-HHMMSS>-<benchmark>-sf<scale_factor>-<short_task_name>
```

Example:

```text
20260626-153000-tpch-sf1-ymatrix-vs-doris
```

## Directory Tree

```text
runs/
  <run_id>/
    manifest.json
    task.yaml
    resolved-task.yaml
    timeline.jsonl
    status.json

    env/
      local.json
      ansible-facts/
      databases/
        ymatrix.json
        doris.json
      hosts/
        172.16.100.29.json
        172.16.100.10.json

    sql/
      schema/
        ymatrix/
        doris/
      queries/
        q01.sql
        q02.sql
        ...
        q22.sql
      explain/
        ymatrix/
        doris/

    data/
      load-manifest.json
      generated-files.txt
      checksums.txt

    benchmark/
      ymatrix/
        load.json
        queries.jsonl
        summary.json
        stdout/
        stderr/
      doris/
        load.json
        queries.jsonl
        summary.json
        stdout/
        stderr/
      comparison.json

    metrics/
      ymatrix/
        system/
        database/
      doris/
        system/
        database/
      samples.jsonl

    perf/
      ymatrix/
        perf-stat.txt
        perf-stat.json
      doris/
        perf-stat.txt
        perf-stat.json

    logs/
      controller.log
      ansible/
      remote/
        ymatrix/
        doris/

    report/
      context.json
      report.md
```

## Top-Level Files

`manifest.json`
: Immutable metadata for the run, including run id, task name, benchmark type, start time, end time, status, target databases, and framework version.

`task.yaml`
: Original task file copied at run start. Secret values should not be stored directly in this file.

`resolved-task.yaml`
: Runtime-resolved task configuration. Secret values must be redacted.

`timeline.jsonl`
: Append-only event stream for each phase. It records phase start, phase end, command execution, query execution, collector start, collector stop, errors, and warnings.

`status.json`
: Current and final run status. This allows interrupted runs to be inspected without parsing logs.

## env/

Stores reproducibility context:

- Local controller information.
- Ansible facts from target hosts.
- Database version and configuration snapshots.
- Host CPU, memory, disk, network, kernel, and OS information.

No benchmark result should live in `env/`; this directory is for environment description only.

## sql/

Stores all SQL used by the run:

- Database-specific TPC-H DDL.
- The exact 22 TPC-H query files.
- EXPLAIN output collected before or during execution.

SQL files are copied into the run directory so future analysis does not depend on the current repository state.

## data/

Stores metadata about generated or reused TPC-H data files:

- Scale factor.
- Data generation tool and version.
- Source directory.
- File list.
- Checksums when available.

Large raw data files do not need to be copied into every run unless the task explicitly enables that behavior.

## benchmark/

Stores benchmark execution results, separated by database.

`queries.jsonl` should contain one JSON object per query execution:

```json
{
  "database": "ymatrix",
  "query": "q01",
  "round": 1,
  "phase": "run",
  "status": "success",
  "start_time": "2026-06-26T15:30:00+08:00",
  "end_time": "2026-06-26T15:30:12+08:00",
  "elapsed_ms": 12000,
  "row_count": 1,
  "error": null
}
```

`summary.json` stores per-database aggregates.

`comparison.json` stores cross-database comparison, including per-query winner, latency ratio, failed query list, and high-level observations.

## metrics/

Stores time-series and sampled metrics collected during the run.

Recommended split:

- `system/`: CPU, memory, disk IO, network IO, process statistics.
- `database/`: database-specific status snapshots.
- `samples.jsonl`: normalized metric stream for report generation.

Collectors should include timestamps and database labels so metrics can be aligned with query execution windows.

## perf/

Stores perf outputs per database target.

v0.1 uses `perf stat` as the default:

- `perf-stat.txt`: raw perf output.
- `perf-stat.json`: parsed perf counters used by reports.

Current perf collection is based on `perf record`, with `perf script` and `perf report` outputs for downstream analysis.

## logs/

Stores controller, Ansible, and remote logs.

Logs are raw evidence. The report may summarize them, but should not replace them.

## report/

Stores report-generation inputs and outputs:

- `context.json`: structured context passed to the Agent.
- `report.md`: final Markdown report.

The report should be reproducible from the files in the run directory.

## Retention Rule

Run directories should be treated as immutable after completion. If a report is regenerated, write a new report file or record the regeneration event in `timeline.jsonl`.
