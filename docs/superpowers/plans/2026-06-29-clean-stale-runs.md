# Clean Stale Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `./automan clean [--job JOB_ID] [-f]` to remove stale running runs whose benchmark process is gone, while preserving completed run results.

**Architecture:** Implement a run-level cleaner separate from job-level `delete`. It scans job plans and status files, detects running runs with no live process, asks for `CLEAN` confirmation unless `-f` is passed, deletes only the stale run/work directories, and rewrites job progress counters/current activity.

**Tech Stack:** Python 3.8 standard library, existing JSON/YAML helpers, `unittest`.

---

### Task 1: Tests

**Files:**
- Create: `tests/test_clean_stale_runs.py`
- Modify: `tests/test_cli_contract.py`

- [ ] Add a test for a job with two successful runs and one stale running run. `clean_stale_runs(..., force=True)` must delete only the stale run/work directories and update totals to success.
- [ ] Add a test that clean requires typing `CLEAN` when `force=False`.
- [ ] Add a CLI contract test for `automan clean --job job1 -f`.

### Task 2: Cleaner

**Files:**
- Create: `automan_core/clean_stale_runs.py`

- [ ] Find candidate jobs from `runs/jobs`.
- [ ] Detect stale runs from `current_run` and per-run `status.json`.
- [ ] Reuse a process matcher that checks `ps -eo pid,args` for the run id.
- [ ] Delete only `run_dir` and `work_dir`, both constrained under the automan root.
- [ ] Remove stale runs from `resolved-plan.yaml` and `job.yaml`.
- [ ] Recompute `job.json` counters and target counters.

### Task 3: CLI

**Files:**
- Modify: `automan_core/cli.py`

- [ ] Add `clean` parser with `--job` and `-f/--force`.
- [ ] Call the cleaner and return non-zero on cancellation or failure.

### Task 4: Verification

**Files:**
- Local only; do not sync to `172.16.100.143`.

- [ ] Run `python -m unittest tests.test_clean_stale_runs tests.test_cli_contract tests.test_progress tests.test_delete_results`.
- [ ] Run `python -m unittest discover`.
