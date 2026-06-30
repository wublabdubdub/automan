# Remove TPC-H Run Mins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove TPC-H `run_mins` so each TPC-H query run ends after complete query stream execution.

**Architecture:** TPC-H keeps `query_streams` as the repeat count and removes duration-based query looping. The config model, run spec, job plan, result output, backend variables, list/report renderers, templates, and tests stop carrying TPC-H `run_mins`; TPC-C and TS behavior remains unchanged.

**Tech Stack:** Python stdlib dataclasses, PyYAML, unittest.

---

### Task 1: Remove TPC-H `run_mins`

**Files:**
- Modify: `conf/tpch/base.yml`
- Modify: `automan_core/models.py`
- Modify: `automan_core/tpch.py`
- Modify: `automan_core/tpch_backend.py`
- Modify: `automan_core/checks.py`
- Modify: `automan_core/report.py`
- Modify: `automan_core/list_results.py`
- Modify: `tests/test_tpch.py`
- Modify: `tests/test_report.py`
- Modify: `tests/test_list_results.py`

- [x] **Step 1: Remove config and model fields**

Delete `tpch.run_mins` from the default template and remove `run_mins` from `TpchConfig` and `TpchRunSpec`.

- [x] **Step 2: Remove matrix expansion and run id segment**

Build TPC-H run specs from target, compress threshold, scale factor, query streams, and stage only. Remove the `-m{run_mins}` run id segment.

- [x] **Step 3: Make internal TPC-H query execution stream-count based**

Run every SQL file for each configured `query_streams` value and finish after the final stream. Do not compute a deadline from minutes.

- [x] **Step 4: Remove TPC-H `run_mins` output fields**

Drop TPC-H `run_mins` from job matrix, resolved task YAML, result JSON, backend variables/results, list rows, and reports.

- [x] **Step 5: Update tests**

Update TPC-H tests to assert no `run_mins` in config/plan/results and no `-m0` in run ids. Keep TPC-C `run_mins` tests intact.

- [x] **Step 6: Run focused tests**

Run `python -m unittest tests.test_tpch tests.test_report tests.test_list_results tests.test_checks`.
