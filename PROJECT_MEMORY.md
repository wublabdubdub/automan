# Project Memory

## Execution Location

The current workspace is the source directory only. It is used for editing project code, configs, docs, templates, and local assets.

Actual benchmark execution must happen on the remote execution host:

```text
host: 72.16.100.143
user: root
password: stored in PROJECT_MEMORY.local.md
remote_path: /root/automan
```

Before running real tests, upload or sync the project from the current source directory to:

```text
root@72.16.100.143:/root/automan
```

All BenchmarkSQL builds, generated work files, benchmark execution, metrics collection, perf collection, and final run artifacts should be produced from the remote path unless explicitly stated otherwise.
