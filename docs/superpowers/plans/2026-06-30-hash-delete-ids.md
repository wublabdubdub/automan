# Hash Delete IDs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `automan list` display short unique hash IDs that `automan delete <id>` can use to delete exactly one run.

**Architecture:** Generate deterministic SHA-1 based IDs from full `run_id` values. Build a global run ID to hash ID map from all job plans so displayed IDs are unique across the current Automan runs tree, extending the hash length only for collisions. Keep full `run_id` and the old semantic short ID as delete compatibility paths.

**Tech Stack:** Python 3.8 standard library `hashlib`, unittest, existing Automan result/job helpers.

---

### Task 1: Add Unique Hash IDs To List

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [x] Add tests that verify displayed row IDs are deterministic hash IDs, not semantic stripped run IDs.
- [x] Implement `result_id_map(root)` and `unique_result_id(root, job_id, run_id)` using SHA-1 prefixes.
- [x] Update every list row builder to use the unique hash ID.
- [x] Run `python -m unittest tests.test_list_results`.

### Task 2: Teach Delete To Resolve Hash IDs

**Files:**
- Modify: `automan_core/delete_results.py`
- Test: `tests/test_delete_results.py`

- [x] Add tests that delete by hash ID and prove only the matching run is removed.
- [x] Keep full `run_id` matching.
- [x] Keep old semantic stripped ID as a compatibility fallback when it matches exactly one run.
- [x] Run `python -m unittest tests.test_delete_results`.

### Task 3: Verify And Sync To 143

**Files:**
- Runtime only.

- [x] Run local focused tests: `python -m unittest tests.test_list_results tests.test_delete_results tests.test_cli_contract`.
- [x] Sync changed files and this plan to `root@172.16.100.143:/root/automan`.
- [x] Run the same focused tests on 143.
- [x] On 143, verify `./automan list -t tpch` shows hash IDs and `./automan delete <hash> -f` can resolve a planned test run or use a dry helper check if no safe deletion target is available.
