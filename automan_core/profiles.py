from __future__ import annotations

from pathlib import Path

from automan_core.config import load_yaml
from automan_core.models import DatabaseProfile


def load_database_types(root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in sorted((root / "configs" / "database-types").glob("*.yaml")):
        data = load_yaml(path)["database_type"]
        result[data["name"]] = data
    return result


def load_database_profiles(root: Path) -> dict[str, DatabaseProfile]:
    result: dict[str, DatabaseProfile] = {}
    base = root / "configs" / "database-profiles"
    for path in sorted(base.rglob("*.yaml")):
        raw = load_yaml(path)["database_profile"]
        tpcc = raw["tpcc"]
        profile = DatabaseProfile(
            id=raw["id"],
            display_name=raw["display_name"],
            database_type=raw["database_type"],
            storage_engine=raw["storage_engine"],
            test_mode=raw["test_mode"],
            ddl_profile=tpcc["ddl_profile"],
            ddl_dir=tpcc["ddl_dir"],
            requires_ddl_confirmation=bool(tpcc.get("requires_ddl_confirmation", False)),
            mars3_defaults=dict(tpcc.get("mars3_defaults", {})),
        )
        result[profile.id] = profile
    return result


def find_profile(
    profiles: dict[str, DatabaseProfile],
    database_type: str,
    storage_engine: str,
    test_mode: str,
) -> DatabaseProfile:
    for profile in profiles.values():
        if (
            profile.database_type == database_type
            and profile.storage_engine == storage_engine
            and profile.test_mode == test_mode
        ):
            return profile
    raise KeyError(f"no profile for {database_type}/{storage_engine}/{test_mode}")

