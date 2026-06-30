from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import yaml


_SCALAR_TYPES = (str, int, float, bool, type(None))


class _AutomanYamlDumper(yaml.SafeDumper):
    pass


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.SequenceNode:
    flow_style = all(isinstance(item, _SCALAR_TYPES) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow_style)


_AutomanYamlDumper.add_representer(list, _represent_list)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            Dumper=_AutomanYamlDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
