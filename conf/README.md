# Automan Pigsty-Style Configs

These files are Ansible-inventory-shaped YAML configs for TPC-C campaigns.
They are intentionally declarative: validation, planning, and parameter rendering
are local operations, while database parameter changes are manual-only.

Shortest path:

```bash
./configure -c tpcc/pg -o automan.yml
./bin/validate -i automan.yml
./automan param -i automan.yml
# review runs/campaigns/<id>/manual-parameter-commands.sh and execute manually
./automan plan -i automan.yml
./automan progress
```

Inventory contract:

- `all.children.bench` describes the execution host that will run automan and BenchmarkSQL.
- Any other child group with `db_type`, `tpcc_warehouses`, and `tpcc_terminals` is treated as one benchmark target.
- The child group name becomes the target id unless `target_id` is set.
- `db_type`, `storage_engine`, and `test_mode` select a profile from `configs/database-profiles`.
- `database_parameters` are recommendations only. Automan renders
  `manual-parameter-commands.sh` and never executes it.
- MARS3 targets can set `mars3_options`; defaults come from the selected database profile.

Common fields:

- Execution host: `ansible_host`, `ansible_user`, `ansible_port`, `automan_workdir` under `bench`.
- DB connection: `db_host`, `db_port`, `db_name`, `db_user`, `db_password`.
- Config host for manual parameter commands: `config_host`, `config_user`, `config_workdir`,
  `postgresql_conf`, `gpconfig_command`, and `restart_command`.
- TPC-C matrix: `tpcc_warehouses`, `tpcc_terminals`, `tpcc_load_workers`, `tpcc_run_mins`.
- Host notes: `host_facts`, `cpu_threads`, and `memory_gb`.

Passwords may be left blank in committed templates and filled in locally. Generated plans redact
passwords, but rendered BenchmarkSQL property files need the real database password to run.
