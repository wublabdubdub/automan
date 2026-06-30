# Fast List Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./automan list -t <type>` return quickly on 143 by filtering unrelated jobs before result parsing and avoiding full reads of large BenchmarkSQL CSV files.

**Architecture:** Keep `automan_core/list_results.py` as the listing entry point. Add an early benchmark gate so `tpcc`, `tpch`, `ts`, and `ap` listing only parse matching job plans. Limit legacy text-source parsing in `automan_core/report.py` and `automan_core/result_summary.py` to logs and small text artifacts; fixed CSV summaries stay parsed through their existing dedicated readers.

**Tech Stack:** Python 3.8 standard library, unittest, existing YAML/JSON helpers.

---

### Task 1: Filter Jobs Before Reading Run Results

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [x] Add a test containing one TPC-C job with a run whose large `result.csv` raises if read, and one TPC-H job. Assert `completed_result_rows(root, benchmark_type="tpch")` returns only the TPC-H row and never touches the TPC-C run artifact.
- [x] In `completed_result_rows`, load each job plan, determine `benchmark = str(plan.get("benchmark", "tpcc"))`, and `continue` when it does not equal the requested `benchmark_type`.
- [x] Run `python -m unittest tests.test_list_results`.

### Task 2: Avoid Full CSV Text Scans

**Files:**
- Modify: `automan_core/report.py`
- Modify: `automan_core/result_summary.py`
- Test: `tests/test_list_results.py`

- [x] Change `_benchmark_text_sources` in both modules so it includes command logs and non-CSV text artifacts from result directories.
- [x] Keep `_merge_result_csv_sources` unchanged so `tx_summary.csv` and `runInfo.csv` remain the only CSV files read for legacy TPC-C metrics.
- [x] Add a list test where a TPC-C run has `tx_summary.csv`, `runInfo.csv`, and a large `result.csv`; assert the row is produced from the two fixed summaries without reading `result.csv`.
- [x] Run `python -m unittest tests.test_list_results tests.test_report`.

### Task 3: Verify And Sync To 143

**Files:**
- Runtime only.

- [x] Run focused local tests: `python -m unittest tests.test_list_results tests.test_report`.
- [x] Sync changed files to `root@172.16.100.143:/root/automan`.
- [x] On 143, stop the two suspended `automan list` jobs if they are still present.
- [x] Verify `./automan list -t tpcc` and `./automan list -t tpch` complete quickly.
