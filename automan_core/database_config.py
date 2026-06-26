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
    results = [ssh.run(script, timeout=300)]
    if results[-1].exit_code == 0:
        results.extend(_verify_params(ssh, connection, params))
    return results


def _apply_ymatrix(ssh: SSHClient, connection: ConnectionInfo, params: dict[str, str]) -> list[CommandResult]:
    results: list[CommandResult] = []
    gpconfig = connection.gpconfig_command or "gpconfig"
    for key, value in params.items():
        results.append(ssh.run(f"{shlex.quote(gpconfig)} -c {shlex.quote(key)} -v {shlex.quote(value)}", timeout=120))
        if results[-1].exit_code != 0:
            return results
    restart = connection.restart_command or "mxstop -afr"
    results.append(ssh.run(restart, timeout=900))
    if results[-1].exit_code == 0:
        results.extend(_verify_params(ssh, connection, params))
    return results


def _verify_params(ssh: SSHClient, connection: ConnectionInfo, params: dict[str, str]) -> list[CommandResult]:
    results: list[CommandResult] = []
    for key, expected in params.items():
        sql = f"show {key};"
        psql = (
            f"PGPASSWORD={shlex.quote(connection.db_password)} psql "
            f"-h {shlex.quote(connection.db_host)} "
            f"-p {shlex.quote(str(connection.db_port))} "
            f"-U {shlex.quote(connection.db_user)} "
            f"-d {shlex.quote(connection.db_name)} "
            f"-tAc {shlex.quote(sql)}"
        )
        command = (
            "for i in $(seq 1 30); do "
            f"{psql} && exit 0; "
            "sleep 5; "
            "done; "
            "exit 1"
        )
        result = ssh.run(command, timeout=180)
        redacted_psql = psql.replace(f"PGPASSWORD={shlex.quote(connection.db_password)}", "PGPASSWORD=***")
        redacted_command = (
            "for i in $(seq 1 30); do "
            f"{redacted_psql} && exit 0; "
            "sleep 5; "
            "done; "
            "exit 1"
        )
        result = CommandResult(
            command=redacted_command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        if result.exit_code == 0:
            actual = _last_nonempty_line(result.stdout)
            if _normalize_param_value(actual) != _normalize_param_value(expected):
                result = CommandResult(
                    command=redacted_command,
                    exit_code=2,
                    stdout=f"expected={expected}\nactual={actual}\nraw={result.stdout}",
                    stderr="parameter value mismatch after restart",
                )
        results.append(result)
        if result.exit_code != 0:
            return results
    return results


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _normalize_param_value(value: str) -> str:
    normalized = value.strip().strip("'\"").lower()
    return "".join(normalized.split())
