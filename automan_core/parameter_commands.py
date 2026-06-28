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
    psql = f"psql -h {connection.db_host} -p {connection.db_port} -U {connection.db_user} -d {connection.db_name}"
    return [
        "# PostgreSQL parameter change commands. Run manually with a role allowed to ALTER SYSTEM.",
        "# Reload or restart PostgreSQL manually when the changed parameter requires it.",
        "# Change commands",
        *[f'{psql} -c "ALTER SYSTEM SET {key} = {_sql_literal(value)};"' for key, value in params.items()],
        "# Confirm commands",
        *[f"{psql} -c 'show {key};'" for key in params],
    ]


def _ymatrix_commands(connection: ConnectionInfo, params: dict[str, str]) -> list[str]:
    gpconfig = connection.gpconfig_command or "gpconfig"
    return [
        f"# Run on config host as the YMatrix admin user: {connection.ssh_user}@{connection.ssh_host}",
        "# Reload or restart YMatrix manually when the changed parameter requires it.",
        "# Change commands",
        *[f"{gpconfig} -c {key} -v {value}" for key, value in params.items()],
        "# Confirm commands",
        *[f"psql -h {connection.db_host} -p {connection.db_port} -U {connection.db_user} -d {connection.db_name} -c 'show {key};'" for key in params],
    ]


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
