# Delete Job Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./automan delete <job_id>` delete an entire job and all run/work artifacts referenced by that job.

**Architecture:** Replace the result-row based delete path with a job-directory based delete path. The command resolves `runs/jobs/<job_id>`, reads `resolved-plan.yaml`, safely enumerates run and BenchmarkSQL work directories, confirms the operation, deletes artifacts, then deletes the job directory.

**Tech Stack:** Python standard library, existing YAML helpers, unittest.

---

### Task 1: Job-Level Delete Core

**Files:**
- Modify: `automan_core/delete_results.py`
- Test: `tests/test_delete_results.py`

- [ ] Update tests so `delete_job(root, "job1", force=True)` removes `runs/jobs/job1`, every `run_dir`, and every `work_dir` from `resolved-plan.yaml`.
- [ ] Implement job lookup through `find_job_dir(root, job_id)`.
- [ ] Use the existing `_safe_path()` guard for every deleted path.
- [ ] Keep confirmation unless `-f/--force` is supplied.
- [ ] Return failure when the job id is unknown.

### Task 2: CLI And Docs

**Files:**
- Modify: `automan_core/cli.py`
- Modify: `README.md`
- Modify: `conf/README.md`
- Modify: `docs/tpcc-automan-flow.md`
- Modify: `docs/user-guide-zh.md`

- [ ] Change delete help text from completed result deletion to job deletion.
- [ ] Remove result-id / `--job` delete examples from user-facing docs.
- [ ] Document `./automan delete <job_id> -f`.

### Task 3: Verification And 143 Sync

**Files:**
- Modify only the files above.

- [ ] Run `python -m unittest tests.test_delete_results tests.test_cli_contract tests.test_list_results`.
- [ ] Run `python -m unittest discover tests`.
- [ ] Sync changed files to `/root/automan` on 143.
- [ ] On 143, create a temporary plan-only job and verify `./automan delete <job_id> -f` removes job/run/work directories.
