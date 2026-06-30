# Inline YAML Matrix Lists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep matrix-like scalar YAML lists in `[a, b]` form when Automan writes generated inventories and plans.

**Architecture:** Centralize the formatting change in `automan_core.config.write_yaml()` so every generated YAML file uses the same serializer. The dumper writes scalar-only lists in flow style while keeping mappings and lists of mappings in block style.

**Tech Stack:** Python stdlib, PyYAML, unittest.

---

### Task 1: Inline Scalar Lists In YAML Output

**Files:**
- Modify: `automan_core/config.py`
- Modify: `tests/test_cli_contract.py`
- Test: `tests/test_cli_contract.py`

- [x] **Step 1: Add a YAML dumper that renders scalar-only lists inline**

Create a `yaml.SafeDumper` subclass and a list representer that sets `flow_style=True` only when every list item is a scalar.

- [x] **Step 2: Use the dumper from `write_yaml()`**

Call `yaml.dump(..., Dumper=_AutomanYamlDumper, allow_unicode=True, sort_keys=False, default_flow_style=False)`.

- [x] **Step 3: Update configure output assertions**

Change the configure contract test from expecting `- 100` to expecting `tpcc_warehouses: [100]` and `tpcc_terminals: [100, 500]`.

- [x] **Step 4: Run focused tests**

Run `python -m unittest tests.test_cli_contract` and verify it passes.
