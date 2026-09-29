"""Write TOML documents from plain values."""

from __future__ import annotations

import json
import re

type TomlValue = str | int | float | bool | list["TomlValue"] | dict[str, "TomlValue"]


def _toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def _toml_value(value: TomlValue) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError("TOML tables are written separately.")


def toml_document(values: dict[str, TomlValue]) -> str:
    lines: list[str] = []

    def write_table(table: dict[str, TomlValue], path: tuple[str, ...]) -> None:
        if path:
            if lines and lines[-1]:
                lines.append("")
            lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")
        for key, value in table.items():
            if not isinstance(value, dict):
                lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                write_table(value, (*path, key))

    write_table(values, ())
    return "\n".join(lines) + "\n"
