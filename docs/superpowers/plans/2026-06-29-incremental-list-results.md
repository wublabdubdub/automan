# Incremental List Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./automan list` show each successful run as soon as that run finishes, even while the parent job is still running.

**Architecture:** Keep job execution unchanged. Publish a small per-run result snapshot at run completion, and let `list` read either that snapshot or the existing BenchmarkSQL logs/result files. Keep failed and incomplete runs out of `list`; those remain visible through `progress`.

**Tech Stack:** Python 3.8 standard library, existing Automan JSON/YAML helpers, `unittest`.

---

### Task 1: Strengthen Incremental Listing Tests

**Files:**
- Modify: `tests/test_list_results.py`

- [ ] Add a test where a running job has one successful run with a per-run result summary and one pending run.
- [ ] Assert `completed_result_rows(root)` and `completed_result_rows(root, "job1")` both include only the finished run.
- [ ] Assert failed or pending runs are not listed.

### Task 2: Publish Per-Run Results

**Files:**
- Modify: `automan_core/executor.py`
- Modify: `automan_core/report.py`

- [ ] After a run finishes successfully, parse its BenchmarkSQL result/log artifacts.
- [ ] Write `runs/<run_id>/result.json` with run id, target id, matrix fields, tpmC, tpmTOTAL, session times, status, and result directory.
- [ ] Keep job status updates unchanged.

### Task 3: Make List Prefer Published Results

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [ ] In `completed_result_rows`, read `runs/<run_id>/result.json` when present.
- [ ] Fall back to existing `_run_result` parsing when no snapshot exists.
- [ ] Continue requiring `status=success`, tpmC, and tpmTOTAL before listing.

### Task 4: Verify Locally Only

**Files:**
- No remote files.

- [ ] Run `python -m unittest tests.test_list_results tests.test_report tests.test_progress`.
- [ ] Run `python -m unittest discover` if focused tests pass quickly.
- [ ] Do not sync to `172.16.100.143`.
