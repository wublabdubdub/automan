# Multi Result Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `automan delete` from job-level deletion to result-ID deletion and allow multiple IDs in one command.

**Architecture:** Resolve each requested ID against run records in `runs/jobs/*/resolved-plan.yaml`, matching both the stable list ID and full run ID. Delete only each matched run/work directory, update the owning job plan files when runs remain, and remove the whole job directory only when all its runs are gone.

**Tech Stack:** Python argparse, pathlib/shutil, existing YAML/JSON helpers, unittest.

---

### Task 1: Result ID Delete Logic

**Files:**
- Modify: `automan_core/delete_results.py`
- Test: `tests/test_delete_results.py`

- [ ] Add tests for deleting multiple result IDs from one job and from multiple jobs.
- [ ] Add tests for unknown IDs and confirmation cancellation.
- [ ] Implement `delete_results(root, result_ids, force=False, input_fn=input)`.
- [ ] Match IDs by stable result ID or full run ID.
- [ ] Delete run/work paths with `_safe_path()`.
- [ ] Rewrite `resolved-plan.yaml`, `job.yaml`, `job.json`, and `status.json` when a job still has remaining runs.
- [ ] Delete the job directory only when no runs remain.

### Task 2: CLI And Docs

**Files:**
- Modify: `automan_core/cli.py`
- Modify: `tests/test_cli_contract.py`
- Modify: `README.md`
- Modify: `docs/tpcc-automan-flow.md`

- [ ] Change parser from `job_id` to `ids` with `nargs="+"`.
- [ ] Call `delete_results(root, args.ids, force=args.force)`.
- [ ] Update CLI contract tests for multiple IDs.
- [ ] Update docs examples to `./automan delete <id> [<id> ...]`.

### Task 3: Verify

**Files:**
- Test: full suite

- [ ] Run `python -m unittest tests.test_delete_results tests.test_cli_contract tests.test_list_results tests.test_ts`.
- [ ] Run `python -m unittest discover`.
