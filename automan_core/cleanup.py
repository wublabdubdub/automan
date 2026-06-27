from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automan_core.models import Target
from automan_core.task_runner import load_task_definition


DROP_TPCC_SQL = r"""
do $$
declare
  item record;
begin
  for item in
    select n.nspname, c.relname, c.relkind
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname = current_schema()
      and c.relname like 'bmsql_%'
  loop
    if item.relkind = 'S' then
      execute format('drop sequence if exists %I.%I cascade', item.nspname, item.relname);
    else
      execute format('drop table if exists %I.%I cascade', item.nspname, item.relname);
    end if;
  end loop;
end $$;
"""


def cleanup_tpcc(root: Path, inventory: Path) -> int:
    task = load_task_definition(root, inventory)
    failures = 0
    for target in task.targets:
        result = _drop_target_objects(root, target)
        if result.returncode == 0:
            print(f"[ OK ] {target.id}: dropped bmsql_% objects on {target.connection.db_host}:{target.connection.db_port}/{target.connection.db_name}")
        else:
            failures += 1
            message = (result.stderr or result.stdout or "cleanup failed").strip()
            print(f"[FAIL] {target.id}: {message}")
    return failures


def _drop_target_objects(root: Path, target: Target) -> subprocess.CompletedProcess[str]:
    command = [
        "psql",
        "-w",
        "-h",
        target.connection.db_host,
        "-p",
        str(target.connection.db_port),
        "-U",
        target.connection.db_user,
        "-d",
        target.connection.db_name,
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        DROP_TPCC_SQL,
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = target.connection.db_password
    return subprocess.run(command, cwd=root, text=True, capture_output=True, env=env, check=False)
