# BenchmarkSQL TPC-C adapter

This directory stores project-owned BenchmarkSQL templates and notes.

The BenchmarkSQL tool itself lives in:

```text
tools/benchmarksql/
```

The TPC-C execution sequence is fixed:

```text
runDatabaseDestroy.sh
runDatabaseBuild.sh
runBenchmark.sh
```

Every TPC-C run must destroy and rebuild the benchmark database before executing the measured benchmark.

