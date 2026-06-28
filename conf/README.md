# Automan Pigsty-Style Configs

These files are Ansible-inventory-shaped YAML configs for TPC-C jobs.
They are intentionally declarative: validation, planning, and parameter rendering
are local operations, while database parameter changes are manual-only.

Shortest path:

```bash
./configure -c pg -o automan.yml
./automan param -i automan.yml
# review the printed change/confirm commands and execute manually
./check.yml -i automan.yml
./tpcc.yml -i automan.yml
./automan list
```

Delete a whole job and all referenced run/work artifacts with:

```bash
./automan delete <job_id>
./automan delete <job_id> -f
```

Use `-f` to skip typing `DELETE`.

Target aliases:

```text
pg        PostgreSQL heap single node
ym-heap   YMatrix heap master only
ym-mars3  YMatrix mars3 master only
```

Compose multiple targets in one job:

```bash
./configure -c pg,ym-heap -o automan.yml
./configure -c pg,ym-heap,ym-mars3 -o automan.yml
```

Maintained inputs are `conf/tpcc/base.yml` plus `conf/tpcc/targets/*.yml`.
Combination templates such as `pg-vs-ymatrix.yml` are not maintained; generate
combinations with comma-separated aliases instead.

Inventory contract:

- `all.children.bench` describes the execution host that will run automan and BenchmarkSQL.
- Any other child group with `db_type` is treated as one benchmark target when shared `tpcc_*` matrix values exist under `all.vars`.
- The child group name becomes the target id unless `target_id` is set.
- `db_type`, `storage_engine`, and `test_mode` select a profile from `configs/database-profiles`.
- `database_parameters` are recommendations only. `./automan param` prints
  the change/confirm commands and never executes them.
- For PostgreSQL/YMatrix auto-filled parameters, `./automan param` probes the target SSH
  host live through `config_host`/`config_user` before rendering memory-sensitive
  values. Use `./automan param --offline` only when you intentionally want to use
  configured `host_facts` without probing.
- MARS3 targets can set `mars3_options`; defaults come from the selected database profile.

Common fields:

- Execution host: `ansible_host`, `ansible_user`, `ansible_port`, `automan_workdir` under `bench`.
- DB connection: `db_host`, `db_port`, `db_name`, `db_user`, `db_password`.
- Remote collector SSH metadata: `config_user`, `config_password`, and `config_workdir`.
  `config_host` is optional and defaults to `db_host`; set it only when the SSH host is
  different from the database connection host.
- Parameter command rendering: PostgreSQL uses `ALTER SYSTEM SET ...`; YMatrix uses
  `gpconfig_command` for `gpconfig -c ... -v ...`. Automan does not render restart commands.
- TPC-C matrix: `tpcc_warehouses`, `tpcc_terminals`, `tpcc_load_workers`, `tpcc_run_mins`.
- Offline host facts: `host_facts`, `cpu_threads`, and `memory_gb`; these are not
  trusted by default parameter rendering unless `--offline` is set.

Passwords may be left blank in committed templates and filled in locally. Generated plans redact
passwords, but rendered BenchmarkSQL property files need the real database password to run.
