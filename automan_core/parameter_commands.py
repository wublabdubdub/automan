from __future__ import annotations

from automan_core.models import ConnectionInfo, DatabaseProfile


def build_manual_parameter_commands(profile: DatabaseProfile, connection: ConnectionInfo, params: dict[str, str]) -> list[str]:
    if not params:
        return []
    if profile.database_type == "postgresql":
        return _postgresql_commands(connection, params)
    if profile.database_type == "ymatrix":
        return _ymatrix_commands(connection, params)
    return [f"# unsupported database type for parameter command generation: {profile.database_type}"]


def _postgresql_commands(connection: ConnectionInfo, params: dict[str, str]) -> list[str]:
    if not connection.postgresql_conf:
        return ["# postgresql_conf is not set; fill it in before generating PostgreSQL parameter commands"]
    block_lines = [f"{key} = '{value}'" for key, value in params.items()]
    restart = connection.restart_command or "pg_ctl restart -D <PGDATA>"
    return [
        f"# Run on config host as the PostgreSQL OS user: {connection.ssh_user}@{connection.ssh_host}",
        f"cp {connection.postgresql_conf} {connection.postgresql_conf}.automan.$(date +%Y%m%d%H%M%S).bak",
        f"python3 - <<'PY'",
        "from pathlib import Path",
        f"path = Path({connection.postgresql_conf!r})",
        "text = path.read_text()",
        "start = '# automan managed settings start'",
        "end = '# automan managed settings end'",
        "block = start + '\\n' + " + repr("\n".join(block_lines)) + " + '\\n' + end + '\\n'",
        "if start in text and end in text:",
        "    before = text.split(start)[0]",
        "    after = text.split(end, 1)[1]",
        "    text = before + block + after.lstrip('\\n')",
        "else:",
        "    text = text.rstrip() + '\\n\\n' + block",
        "path.write_text(text)",
        "PY",
        restart,
        *[f"psql -h {connection.db_host} -p {connection.db_port} -U {connection.db_user} -d {connection.db_name} -c 'show {key};'" for key in params],
    ]


def _ymatrix_commands(connection: ConnectionInfo, params: dict[str, str]) -> list[str]:
    gpconfig = connection.gpconfig_command or "gpconfig"
    restart = connection.restart_command or "mxstop -afr"
    return [
        f"# Run on config host as the YMatrix admin user: {connection.ssh_user}@{connection.ssh_host}",
        *[f"{gpconfig} -c {key} -v {value}" for key, value in params.items()],
        restart,
        *[f"psql -h {connection.db_host} -p {connection.db_port} -U {connection.db_user} -d {connection.db_name} -c 'show {key};'" for key in params],
    ]
