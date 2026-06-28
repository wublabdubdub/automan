# Stable Result Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stable result IDs to `automan list` and add `automan delete <id>` with `-f` to remove one listed run result and its generated artifacts.

**Architecture:** Reuse `completed_result_rows()` as the only source of deletable records. A stable ID is derived from the run id by stripping the `<job_id>-` prefix, for example `pg31-w100-c500`; `delete` accepts either that stable ID or the full run id. Deletion removes the run directory, BenchmarkSQL work directory, stale report directory, and updates the owning job plan/state files.

**Tech Stack:** Python standard library, existing YAML/JSON helpers, `unittest`.

---

### Task 1: Add Stable IDs To List Rows

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [x] Add a helper that returns `run_id[len(job_id)+1:]` when the run id starts with `<job_id>-`; otherwise it returns the full run id.
- [x] Add `id` to each completed result row.
- [x] Add the `ID` column to both global and `--job` list output.
- [x] Extend the list test to assert `pg-w100-c100` appears in output and in the returned row.

### Task 2: Add Delete Logic

**Files:**
- Create: `automan_core/delete_results.py`
- Test: `tests/test_delete_results.py`

- [x] Resolve records by stable ID or full run id using `completed_result_rows()`.
- [x] Return a clear failure when no record matches.
- [x] Return a clear failure when multiple jobs match and the user omitted `--job`.
- [x] Before deletion, print the job, run id, run directory, and work directory.
- [x] Without `-f`, require the user to type `DELETE`.
- [x] Delete only paths that resolve under the current automan root.
- [x] Remove the run from `resolved-plan.yaml` and `job.yaml`.
- [x] Recalculate `job.json` counts from remaining run statuses.
- [x] Remove the stale report directory.
- [x] If the deleted run was the last run in the job, delete the whole job directory.

### Task 3: Wire CLI And Docs

**Files:**
- Modify: `automan_core/cli.py`
- Modify: `README.md`
- Modify: `conf/README.md`
- Modify: `docs/tpcc-automan-flow.md`

- [x] Add parser shape `./automan delete <id> [--job JOB] [-f]`.
- [x] Call `delete_completed_result()` from `cli.main()`.
- [x] Document that `-f` skips the interactive `DELETE` confirmation.
- [x] Keep `--yes` unsupported.

### Task 4: Verify

**Files:**
- Test: full suite

- [x] Run `python -m unittest discover -v`.
- [x] Sync changed files to `/root/automan` on `172.16.100.143`.
- [x] Run `python3.8 -m unittest discover -v` on `172.16.100.143`.
- [x] Verify `./automan list --job pg31_tpcc_100w_100_500_1000_15m` shows stable IDs.
