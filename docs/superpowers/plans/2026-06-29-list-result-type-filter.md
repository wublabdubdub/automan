# List Result Type Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `automan list -t tpcc|ts` so TPC-C and TS results render with their own columns.

**Architecture:** Keep result collection in `automan_core/list_results.py`, adding a benchmark type filter that defaults to `tpcc`. The CLI passes `args.type` to the list renderer, and table selection stays based on the filtered rows.

**Tech Stack:** Python argparse, unittest, existing Automan result JSON/YAML helpers.

---

### Task 1: Add CLI Type Filter

**Files:**
- Modify: `automan_core/cli.py`
- Modify: `automan_core/list_results.py`
- Test: `tests/test_list_results.py`

- [ ] **Step 1: Write tests for default TPC-C and explicit TS**

Add tests that create one TPC-C job and one TS job. Assert `show_completed_results(root)` prints TPC-C columns and not TS rows, then assert `show_completed_results(root, benchmark_type="ts")` prints TS columns and not TPC-C rows.

- [ ] **Step 2: Add `-t/--type` to the parser**

Add `list_parser.add_argument("-t", "--type", choices=["tpcc", "ts"], default="tpcc", help="benchmark type to list")`, then pass `benchmark_type=args.type` to `show_completed_results`.

- [ ] **Step 3: Filter collected rows by benchmark type**

Change `show_completed_results()` and `completed_result_rows()` to accept `benchmark_type: str = "tpcc"`. Skip rows whose detected benchmark does not match that type.

- [ ] **Step 4: Verify**

Run `python -m unittest tests.test_list_results tests.test_cli_contract tests.test_ts`.
Expected: all tests pass.
