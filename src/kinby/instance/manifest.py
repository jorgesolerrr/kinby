"""Parse and validate an instance manifest."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, ValidationError

from kinby.instance.dataclasses import (
    Conventions,
    Instance,
    Manifest,
    MatchingRule,
    Memory,
    Models,
    Workspace,
)
from kinby.instance.errors import ManifestError
from kinby.instance.layout import ENV_NAME, MANIFEST_NAME, STATE_DIR, WORKSPACE_DIR

_MODEL_PATTERN = re.compile(r"^[^:\s]+:[^:\s]+$")


def _provider_model(value: str) -> str:
    if not _MODEL_PATTERN.fullmatch(value):
        raise ValueError("must use provider:model form")
    return value


NonEmpty = Annotated[str, StringConstraints(min_length=1)]
ModelName = Annotated[NonEmpty, AfterValidator(_provider_model)]


class _Section(BaseModel):
    """One TOML table. Unknown keys and wrong types are rejected before any code reads them."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RawModels(_Section):
    main: ModelName
    recap: ModelName | None = None
    embed: ModelName | None = None


class RawConventions(_Section):
    enabled: bool = False
    instructions: list[NonEmpty] = ["AGENTS.md"]
    skills: list[NonEmpty] = [".agents/skills"]


class RawWorkspace(_Section):
    path: NonEmpty = WORKSPACE_DIR
    source: NonEmpty | None = None
    conventions: RawConventions = RawConventions()


class RawMemory(_Section):
    pass


class RawManifest(_Section):
    """The shape of ``kinby.toml``, validated once at load."""

    id: NonEmpty
    persona_name: NonEmpty | None = None
    state_dir: NonEmpty = STATE_DIR
    models: RawModels
    workspace: RawWorkspace = RawWorkspace()
    memory: RawMemory = RawMemory()


def _manifest_error(exc: ValidationError) -> ManifestError:
    first = exc.errors()[0]
    key = ".".join(str(part) for part in first["loc"])
    message = first["msg"].removeprefix("Value error, ")
    return ManifestError(f"{key}: {message}")


def _resolved_path(base: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _existing_workspace_paths(
    workspace_path: Path, entries: list[str], *, directory: bool
) -> tuple[Path, ...]:
    found: list[Path] = []
    for entry in entries:
        resolved = _resolved_path(workspace_path, entry)
        exists = resolved.is_dir() if directory else resolved.is_file()
        if exists:
            found.append(resolved)
    return tuple(found)


def _conventions(workspace_path: Path, raw: RawConventions) -> Conventions:
    if not raw.enabled:
        return Conventions(instructions=(), skills=())
    return Conventions(
        instructions=_existing_workspace_paths(workspace_path, raw.instructions, directory=False),
        skills=_existing_workspace_paths(workspace_path, raw.skills, directory=True),
    )


def _manifest(instance_path: Path, raw: RawManifest, model_override: str | None) -> Manifest:
    try:
        main = _provider_model(model_override) if model_override is not None else raw.models.main
    except ValueError as exc:
        raise ManifestError(f"models.main: {exc}") from exc
    workspace_path = _resolved_path(instance_path, raw.workspace.path)
    return Manifest(
        id=raw.id,
        persona_name=raw.persona_name,
        state_dir=_resolved_path(instance_path, raw.state_dir),
        models=Models(main=main, recap=raw.models.recap or main, embed=raw.models.embed),
        workspace=Workspace(
            path=workspace_path,
            source=raw.workspace.source,
            conventions=_conventions(workspace_path, raw.workspace.conventions),
        ),
        memory=Memory(),
    )


def load_instance(
    directory: Path,
    *,
    matching_rule: MatchingRule = "explicit directory",
    model_override: str | None = None,
) -> Instance:
    instance_path = Path(directory).resolve()
    load_dotenv(instance_path / ENV_NAME, override=False)
    manifest_path = instance_path / MANIFEST_NAME
    try:
        with manifest_path.open("rb") as manifest_file:
            raw = RawManifest.model_validate(tomllib.load(manifest_file))
    except FileNotFoundError as exc:
        raise ManifestError(f"{MANIFEST_NAME}: not found in {instance_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{MANIFEST_NAME}: {exc}") from exc
    except ValidationError as exc:
        raise _manifest_error(exc) from exc
    return Instance(
        path=instance_path,
        manifest=_manifest(instance_path, raw, model_override),
        matching_rule=matching_rule,
    )


def reload_manifest(instance: Instance, *, model_override: str | None = None) -> Manifest:
    return load_instance(
        instance.path,
        matching_rule=instance.matching_rule,
        model_override=model_override,
    ).manifest
