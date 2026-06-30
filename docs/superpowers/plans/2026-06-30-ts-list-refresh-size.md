# TS List Refresh Size Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `./automan list -t ts --refresh-size` to query current TS table size and update cached result metadata.

**Architecture:** Keep default list behavior as a file-only snapshot read. Add an opt-in refresh path that loads an inventory with real target credentials, maps targets by ID, reuses TS table-size SQL, and writes refreshed size fields back to each successful TS run result.

**Tech Stack:** Python stdlib, existing `automan_core` YAML helpers, existing TS `_table_data_size` helper, unittest.

---

### Task 1: CLI Flags

**Files:**
- Modify: `automan_core/cli.py`
- Test: `tests/test_cli_contract.py`

- [x] Add `-i/--inventory` and `--refresh-size` to the `list` parser.
- [x] Pass the parsed values into `show_completed_results`.

### Task 2: Refresh Implementation

**Files:**
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [x] Extend `show_completed_results` and `completed_result_rows` with `refresh_size`, `inventory_path`, and injectable runner arguments.
- [x] Load real targets from the inventory only when refresh is requested.
- [x] Reuse the TS table-size query for each TS row and update `result.json` plus `database/table-size.json` on success.
- [x] Preserve old snapshot values when refresh cannot be performed.

### Task 3: Verification

**Files:**
- Test: `tests/test_list_results.py`
- Test: `tests/test_cli_contract.py`

- [x] Add a unit test proving refresh replaces `289 MB` with a live value.
- [x] Add a unit test proving refreshed size is written back to `result.json`.
- [x] Run focused tests for list results, CLI contract, and TS behavior.
