# YMatrix TPC-C Parameter Auto-Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate complete manual YMatrix master-only TPC-C parameter commands, including PG-compatible memory/WAL parameters computed from target host facts.

**Architecture:** Add a parameter merge helper at inventory/legacy target loading time so explicit user `database_parameters` override generated recommendations while missing YMatrix TPC-C parameters are filled automatically. Keep execution manual-only by reusing the existing `build_manual_parameter_commands()` path.

**Tech Stack:** Python dataclasses and YAML inventory parsing, `unittest`, existing `gpconfig` manual command renderer.

---

### Task 1: Add tests for YMatrix auto-filled parameters

**Files:**
- Modify: `tests/test_inventory_config.py`

- [ ] Add an inventory test with `db_type: ymatrix`, `storage_engine: mars3`, `test_mode: master_only`, `host_facts.memory_gb: 251`, and only `max_connections` explicitly set.
- [ ] Assert generated commands include `gpconfig -c shared_buffers -v 64GB`, `effective_cache_size -v 128GB`, `work_mem -v 4MB`, `maintenance_work_mem -v 2GB`, `checkpoint_completion_target -v 0.9`, `max_wal_size -v 64GB`, `min_wal_size -v 8GB`, and `vacuum_cost_limit -v 10000`.
- [ ] Assert user-provided `max_connections` remains unchanged.

### Task 2: Implement YMatrix TPC-C recommendations

**Files:**
- Modify: `automan_core/sizing.py`
- Modify: `automan_core/task_runner.py`

- [ ] Update `recommend_params()` so YMatrix returns PG-compatible TPC-C parameters for master-only use.
- [ ] Add a helper that merges generated recommendations with explicit `database_parameters`, preserving explicit values.
- [ ] Use that helper in both legacy and inventory target loading.

### Task 3: Keep templates and documentation aligned

**Files:**
- Modify: `conf/tpcc/ymatrix-heap.yml`
- Modify: `conf/tpcc/ymatrix-mars3.yml`
- Modify: `conf/tpcc/pg-vs-ymatrix.yml`

- [ ] Add comments documenting that YMatrix missing TPC-C memory/WAL parameters are auto-filled from `host_facts`.
- [ ] Keep `max_connections` visible as an explicit override example.

### Task 4: Verify

**Files:**
- Run tests only.

- [ ] Run `python -m unittest tests.test_inventory_config tests.test_task_runner tests.test_profiles_and_plan`.
- [ ] Confirm generated YMatrix manual commands use `gpconfig -c <param> -v <value>`.
