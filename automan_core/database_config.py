from __future__ import annotations

import shlex

from automan_core.models import ConnectionInfo, DatabaseProfile
from automan_core.ssh import SSHClient, CommandResult


def apply_database_params(profile: DatabaseProfile, connection: ConnectionInfo, params: dict[str, str]) -> list[CommandResult]:
    ssh = SSHClient(connection.ssh_host, connection.ssh_port, connection.ssh_user, connection.ssh_password)
    if profile.database_type == "postgresql":
        return _apply_postgresql(ssh, connection, params)
    if profile.database_type == "ymatrix":
        return _apply_ymatrix(ssh, connection, params)
    raise ValueError(f"unsupported database type: {profile.database_type}")


def _apply_postgresql(ssh: SSHClient, connection: ConnectionInfo, params: dict[str, str]) -> list[CommandResult]:
    if not connection.postgresql_conf:
        raise ValueError("postgresql_conf is required for PostgreSQL parameter changes")
    if not connection.restart_command:
        raise ValueError("restart_command is required for PostgreSQL parameter changes")

    assignments = "\n".join(f"{key} = '{value}'" for key, value in params.items())
    marker_start = "# automan managed settings start"
    marker_end = "# automan managed settings end"
    script = f"""
set -e
cp {shlex.quote(connection.postgresql_conf)} {shlex.quote(connection.postgresql_conf)}.automan.$(date +%Y%m%d%H%M%S).bak
python3 - <<'PY'
from pathlib import Path
path = Path({connection.postgresql_conf!r})
text = path.read_text()
start = {marker_start!r}
end = {marker_end!r}
block = start + "\\n" + {assignments!r} + "\\n" + end + "\\n"
if start in text and end in text:
    before = text.split(start)[0]
    after = text.split(end, 1)[1]
    text = before + block + after.lstrip("\\n")
else:
    text = text.rstrip() + "\\n\\n" + block
path.write_text(text)
PY
{connection.restart_command}
"""
    return [ssh.run(script, timeout=300)]


def _apply_ymatrix(ssh: SSHClient, connection: ConnectionInfo, params: dict[str, str]) -> list[CommandResult]:
    results: list[CommandResult] = []
    gpconfig = connection.gpconfig_command or "gpconfig"
    for key, value in params.items():
        results.append(ssh.run(f"{shlex.quote(gpconfig)} -c {shlex.quote(key)} -v {shlex.quote(value)}", timeout=120))
        if results[-1].exit_code != 0:
            return results
    restart = connection.restart_command or "mxstop -afr"
    results.append(ssh.run(restart, timeout=900))
    return results

