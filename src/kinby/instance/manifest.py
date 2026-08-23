"""Parse and validate an instance manifest."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 test run
    import tomli as tomllib

from kinby.instance.dataclasses import Instance, Manifest, Memory, Models, Workspace
from kinby.instance.errors import ManifestError
from kinby.instance.layout import ENV_NAME, MANIFEST_NAME, STATE_DIR, WORKSPACE_DIR

_MODEL_PATTERN = re.compile(r"^[^:\s]+:[^:\s]+$")


def _reject_unknown(values: Mapping[str, Any], allowed: set[str], prefix: str = "") -> None:
    for key in values:
        if key not in allowed:
            qualified_key = f"{prefix}.{key}" if prefix else key
            raise ManifestError(f"{qualified_key}: unknown key")


def _table(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{key}: must be a table")
    return value


def _required_string(values: Mapping[str, Any], key: str, prefix: str = "") -> str:
    qualified_key = f"{prefix}.{key}" if prefix else key
    if key not in values:
        raise ManifestError(f"{qualified_key}: required")
    value = values[key]
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{qualified_key}: must be a non-empty string")
    return value


def _optional_string(values: Mapping[str, Any], key: str, prefix: str = "") -> str | None:
    if key not in values:
        return None
    return _required_string(values, key, prefix)


def _model(value: str, key: str) -> str:
    if not _MODEL_PATTERN.fullmatch(value):
        raise ManifestError(f"{key}: must use provider:model form")
    return value


def _resolved_path(instance_path: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return (instance_path / path).resolve()


def _parse_manifest(instance_path: Path, values: Mapping[str, Any]) -> Manifest:
    _reject_unknown(
        values,
        {"id", "persona_name", "state_dir", "models", "workspace", "memory"},
    )
    instance_id = _required_string(values, "id")
    persona_name = _optional_string(values, "persona_name")
    state_dir_value = _optional_string(values, "state_dir") or STATE_DIR

    if "models" not in values:
        raise ManifestError("models.main: required")
    model_values = _table(values["models"], "models")
    _reject_unknown(model_values, {"main", "recap", "embed"}, "models")
    main = _model(_required_string(model_values, "main", "models"), "models.main")
    recap_value = _optional_string(model_values, "recap", "models") or main
    recap = _model(recap_value, "models.recap")
    embed_value = _optional_string(model_values, "embed", "models")
    embed = _model(embed_value, "models.embed") if embed_value is not None else None

    workspace_values = _table(values.get("workspace", {}), "workspace")
    _reject_unknown(workspace_values, {"path", "source"}, "workspace")
    workspace_path_value = _optional_string(workspace_values, "path", "workspace") or WORKSPACE_DIR
    source = _optional_string(workspace_values, "source", "workspace")

    memory_values = _table(values.get("memory", {}), "memory")
    _reject_unknown(memory_values, set(), "memory")

    return Manifest(
        id=instance_id,
        persona_name=persona_name,
        state_dir=_resolved_path(instance_path, state_dir_value),
        models=Models(main=main, recap=recap, embed=embed),
        workspace=Workspace(
            path=_resolved_path(instance_path, workspace_path_value),
            source=source,
        ),
        memory=Memory(),
    )


def load_instance(directory: Path, *, resolved_by: str = "explicit directory") -> Instance:
    """Load an instance from an explicit directory."""
    instance_path = Path(directory).resolve()
    load_dotenv(instance_path / ENV_NAME, override=False)
    manifest_path = instance_path / MANIFEST_NAME
    try:
        with manifest_path.open("rb") as manifest_file:
            values = tomllib.load(manifest_file)
    except FileNotFoundError as exc:
        raise ManifestError(f"{MANIFEST_NAME}: not found in {instance_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{MANIFEST_NAME}: {exc}") from exc
    return Instance(
        path=instance_path,
        manifest=_parse_manifest(instance_path, values),
        resolved_by=resolved_by,
    )
