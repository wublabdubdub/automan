# Pigsty Style Automan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild automan into a Pigsty-style benchmark operations toolkit for TPC-C, with config-driven execution, Ansible playbooks, clear CLI output, manual-only DB parameter commands, progress tracking, and reproducible run archives.

**Architecture:** Keep Python as the local control plane for validation, planning, rendering, progress, and report context. Move remote operational actions into Ansible playbooks and roles. Make `conf/*.yml` the main user-facing contract, with root-level scripts/playbooks as direct operation entry points.

**Tech Stack:** Python 3.8+, Ansible, YAML inventory/config, BenchmarkSQL, shell wrappers, PostgreSQL/YMatrix psql-compatible connections, Markdown reports.

---

## File Structure

- Create `configure`: generate `automan.yml` from `conf/` templates, Pigsty-style output.
- Create `check.yml`, `tpcc.yml`, `tpcc-rm.yml`, `report.yml`: root playbook entry points.
- Create `playbooks/*.yml`: implementation playbooks imported by root wrappers.
- Create `roles/bench_tool`, `roles/db_check`, `roles/tpcc_prepare`, `roles/tpcc_run`, `roles/collector`, `roles/archive`, `roles/report`: Ansible role boundaries.
- Create `conf/tpcc/*.yml`: commented task inventories for PostgreSQL, YMatrix heap, YMatrix mars3, and PG-vs-YMatrix.
- Create `bin/validate`, `bin/progress`, `bin/render-param`: practical command shortcuts.
- Modify `automan`: keep as Python CLI wrapper with Pigsty-like subcommands.
- Modify `automan_core/cli.py`: add `validate`, `param`, `plan`, `progress`, and compatibility `run`.
- Modify `automan_core/task_runner.py`: load Pigsty-style inventory and old task YAML during transition.
- Modify `automan_core/executor.py`: create BenchmarkSQL result parent dirs and classify false-success logs.
- Modify `automan_core/progress.py`: show last error and current phase clearly.
- Modify `automan_core/plan.py`: archive manual commands, resolved config, and per-run task files.
- Add tests for inventory loading, parameter rendering, false-success detection, and result-dir creation.

## Task 1: Pigsty-Style Config Contract

**Files:**
- Create: `conf/README.md`
- Create: `conf/tpcc/pg.yml`
- Create: `conf/tpcc/ymatrix-heap.yml`
- Create: `conf/tpcc/ymatrix-mars3.yml`
- Create: `conf/tpcc/pg-vs-ymatrix.yml`
- Modify: `automan_core/task_runner.py`
- Test: `tests/test_inventory_config.py`

- [ ] Write tests that load `conf/tpcc/pg.yml` and assert one target, matrix expansion, password support, and manual parameter command generation.
- [ ] Implement inventory-style parsing with `all.children.bench` for execution host and any child group with `db_type` plus `tpcc_*` vars as a benchmark target.
- [ ] Preserve support for existing `configs/tasks/*.yaml` so current templates do not break immediately.
- [ ] Run `python -m unittest tests.test_inventory_config -v`.

## Task 2: Root Entry Points and Output Style

**Files:**
- Create: `configure`
- Create: `bin/validate`
- Create: `bin/progress`
- Create: `bin/render-param`
- Modify: `automan`
- Modify: `automan_core/cli.py`
- Test: `tests/test_cli_contract.py`

- [ ] Add Pigsty-style log helpers that print `[ OK ]`, `[WARN]`, `[FAIL]`, and `[HINT]`.
- [ ] `./configure -c tpcc/pg -o automan.yml` copies `conf/tpcc/pg.yml` to `automan.yml` and prints the selected template and output path.
- [ ] `./bin/validate -i automan.yml` validates YAML shape, execution host, DB target fields, and TPC-C matrix without connecting to DB.
- [ ] `./automan param -i automan.yml` generates `manual-parameter-commands.sh` under a new campaign and never executes it.
- [ ] `./automan progress` remains available and readable.
- [ ] Run CLI contract tests and `python -m automan_core --help`.

## Task 3: Ansible Playbooks and Roles

**Files:**
- Create: `ansible.cfg`
- Create: `check.yml`
- Create: `tpcc.yml`
- Create: `tpcc-rm.yml`
- Create: `report.yml`
- Create: `playbooks/check.yml`
- Create: `playbooks/tpcc.yml`
- Create: `playbooks/tpcc-rm.yml`
- Create: `playbooks/report.yml`
- Create role task files under `roles/*/tasks/main.yml`

- [ ] Root playbooks should be executable and import their `playbooks/` implementation.
- [ ] `check.yml` verifies Java, psql, BenchmarkSQL dist, work directories, and DB connectivity.
- [ ] `tpcc.yml` calls Python planner first, then executes per-run shell commands through Ansible on the execution host.
- [ ] `tpcc-rm.yml` performs explicit TPC-C object cleanup only when invoked.
- [ ] `report.yml` generates a Markdown skeleton from archived run files.
- [ ] Add tags matching Pigsty style: `check`, `bench_tool`, `db_check`, `tpcc_prepare`, `tpcc_run`, `collector`, `archive`, `report`.

## Task 4: TPC-C Runtime Correctness

**Files:**
- Modify: `automan_core/executor.py`
- Modify: `automan_core/plan.py`
- Modify: `automan_core/progress.py`
- Test: `tests/test_executor.py`
- Test: `tests/test_progress.py`

- [ ] Create `runs/<run_id>/benchmark` before `runBenchmark.sh`.
- [ ] Scan command stdout/stderr for fatal patterns: `FATAL:`, `ERROR:`, `Exception`, `Failed to`, `password authentication failed`.
- [ ] Treat fatal patterns as phase failure even when BenchmarkSQL scripts exit 0.
- [ ] Record `last_error` in run status and campaign progress.
- [ ] Ensure first-run destroy policy is `schema_probe`; destroy only when existing `bmsql_*` objects are found.
- [ ] Run executor and progress tests.

## Task 5: Reports and Archives

**Files:**
- Create: `templates/report.md.j2`
- Modify: `automan_core/plan.py`
- Create or modify: `automan_core/report.py`
- Test: `tests/test_report.py`

- [ ] Archive `automan.yml`, `resolved-plan.yaml`, generated properties, DDL profile, command logs, and manual parameter commands.
- [ ] Generate `runs/campaigns/<campaign_id>/report/report.md`.
- [ ] Report includes target, DB connection redacted, matrix, parameter command path, run status summary, and failure snippets.
- [ ] Run report tests.

## Task 6: Documentation and Migration

**Files:**
- Create: `README.md`
- Modify: `target.md`
- Modify: `docs/tpcc-automan-flow.md`
- Modify: `PROJECT_MEMORY.md`

- [ ] Document the shortest path: `./configure`, `./bin/validate`, `./automan param`, manual parameter execution, `./check.yml`, `./tpcc.yml`, `./automan progress`, `./report.yml`.
- [ ] Explicitly state that DB parameter changes are manual-only.
- [ ] Explicitly state execution host: `172.16.100.143:/root/automan`.
- [ ] Keep old task YAML as legacy examples or mark them deprecated.

## Task 7: Verification and Remote Sync

**Files:**
- No new production files.

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `./configure -c tpcc/pg -o automan.yml` on Linux or equivalent Python tests locally.
- [ ] Sync to GitHub `wublabdubdub/automan`.
- [ ] On `172.16.100.143:/root/automan`, pull latest, run tests, run `./bin/validate -i conf/tpcc/pg.yml`, run `./automan param -i conf/tpcc/pg.yml`, and run `./check.yml -i conf/tpcc/pg.yml`.
- [ ] Do not start a long benchmark unless explicitly requested after check succeeds.

## Self-Review

- Coverage: Config contract, direct root commands, Ansible roles, TPC-C correctness, progress, reports, docs, and remote verification are all represented.
- Placeholder scan: No task uses TBD/TODO language as an implementation substitute.
- Type consistency: Public CLI consistently uses `-i` for inventory/config, while legacy `run --task` remains a transition path.
