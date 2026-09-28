"""Parse and validate an instance manifest."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tomlkit
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    WithJsonSchema,
    field_validator,
)

from kinby.contracts import ManifestValues, NewModelPrice
from kinby.instance.dataclasses import (
    Budgets,
    Conventions,
    Feedback,
    FeedbackPolicy,
    Instance,
    Manifest,
    MatchingRule,
    Memory,
    ModelPrice,
    Models,
    PackageProvenance,
    RecapPolicy,
    Routines,
    Serve,
    Tools,
    Workspace,
)
from kinby.instance.errors import ManifestError
from kinby.instance.layout import ENV_NAME, MANIFEST_NAME, STATE_DIR, WORKSPACE_DIR

# Unlike $, this absolute-end assertion rejects a trailing newline in Python and JSON Schema.
_MODEL_PATTERN = re.compile(r"^[^:\s]+:[^:\s]+(?![\s\S])")
_LISTEN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_LISTEN_HOST_PATTERN = rf"{_LISTEN_LABEL}(?:\.{_LISTEN_LABEL})*"
_LISTEN_HOST = re.compile(rf"^{_LISTEN_HOST_PATTERN}$")
_LISTEN_PATTERN = rf"^{_LISTEN_HOST_PATTERN}:[0-9]+(?![\s\S])"
_MODEL_ERROR = "must use provider:model form"
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
ModelName = Annotated[
    str,
    StringConstraints(min_length=1, pattern=_MODEL_PATTERN),
]
_MODEL_NAME_ADAPTER = TypeAdapter(ModelName)


def api_key_variable(model: ModelName) -> str:
    """Where the model's provider looks for its key, by the `<PROVIDER>_API_KEY` convention."""
    provider, _, _ = model.partition(":")
    return f"{provider.upper()}_API_KEY"


def _provider_model(value: str) -> str:
    try:
        return _MODEL_NAME_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise ValueError(_MODEL_ERROR) from exc


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
    snapshots: bool = True
    conventions: RawConventions = RawConventions()


class RawMemory(_Section):
    recap: RecapPolicy = Field(default=RecapPolicy.EVERY_TURN, strict=False)


class RawFeedback(_Section):
    ask: FeedbackPolicy = Field(default=FeedbackPolicy.EVERY_TURN, strict=False)


class RawTools(_Section):
    defaults: bool = True
    bash_timeout_seconds: Annotated[int, Field(gt=0)] = 120


class RawBudgets(_Section):
    steps: Annotated[int, Field(gt=0)] | None = None
    tokens: Annotated[int, Field(gt=0)] | None = None
    seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    usd_per_day: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None


class RawModelPrice(_Section):
    input: Annotated[float, Field(ge=0)]
    output: Annotated[float, Field(ge=0)]
    cache_read: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    cache_write: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class RawRoutines(_Section):
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA time zone: {value}") from exc
        return value


def parse_listen(value: object) -> Serve:
    if isinstance(value, Serve):
        return value
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError("must be a host:port address")
    host, separator, port_text = value.rpartition(":")
    if not separator or _LISTEN_HOST.fullmatch(host) is None or not port_text.isdigit():
        raise ValueError("must be a host:port address")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("must be a host:port address")
    return Serve(host=host, port=port)


ListenAddress = Annotated[
    Serve,
    BeforeValidator(parse_listen),
    WithJsonSchema({"title": "Listen", "type": "string", "pattern": _LISTEN_PATTERN}),
]


class RawServe(_Section):
    listen: ListenAddress


class RawPackage(_Section):
    id: NonEmpty
    distribution: NonEmpty
    version: NonEmpty


class RawManifest(_Section):
    """The shape of ``kinby.toml``, validated once at load."""

    id: NonEmpty
    persona_name: NonEmpty | None = None
    state_dir: NonEmpty = STATE_DIR
    models: RawModels
    workspace: RawWorkspace = RawWorkspace()
    memory: RawMemory = RawMemory()
    feedback: RawFeedback = RawFeedback()
    tools: RawTools = RawTools()
    routines: RawRoutines = RawRoutines()
    serve: RawServe | None = None
    package: RawPackage | None = None
    budgets: RawBudgets = Field(default_factory=RawBudgets, title="Budgets")
    prices: dict[ModelName, RawModelPrice] = Field(
        default_factory=dict,
        json_schema_extra={"additionalProperties": False},
    )


def parse_manifest(text: str) -> RawManifest:
    """Validate the text of ``kinby.toml`` without loading the instance it belongs to."""
    return RawManifest.model_validate(tomllib.loads(text))


def offered_values(raw: RawManifest) -> ManifestValues:
    """The fields of the manifest a client may change."""
    return ManifestValues.model_validate(raw.model_dump(include=set(ManifestValues.model_fields)))


def edited_manifest(text: str, values: ManifestValues, prices: Mapping[str, NewModelPrice]) -> str:
    """Write *values* and *prices* into the text of ``kinby.toml``, keeping its comments.

    Only the fields whose value changes are touched, so a field the file leaves to its
    default stays out of it. Raises ``ValidationError`` when the result is not a valid manifest.
    """
    current = offered_values(parse_manifest(text)).model_dump(mode="json")
    document = tomlkit.parse(text)
    for key, value in values.model_dump(mode="json").items():
        if not isinstance(value, dict):
            if value != current[key]:
                _put(document, key, value)
            continue
        for name, field_value in value.items():
            if field_value != current[key][name]:
                if key not in document:
                    document[key] = tomlkit.table()
                _put(document[key], name, field_value)
    if prices:
        if "prices" not in document:
            document["prices"] = tomlkit.table(is_super_table=True)
        for model, price in prices.items():
            if model not in document["prices"]:
                document["prices"][model] = tomlkit.table()
            document["prices"][model].update(price.model_dump())
    edited = tomlkit.dumps(document)
    parse_manifest(edited)
    return edited


def _put(table: MutableMapping[str, object], key: str, value: object) -> None:
    if value is None:
        table.pop(key, None)
    else:
        table[key] = value


def manifest_field_errors(exc: ValidationError) -> dict[str, str]:
    """What is wrong with each field of a manifest, by its dotted key."""
    fields: dict[str, str] = {}
    for error in exc.errors():
        key = ".".join(str(part) for part in error["loc"])
        ctx = error.get("ctx") or {}
        if ctx.get("pattern") == _MODEL_PATTERN.pattern:
            message = _MODEL_ERROR
        else:
            message = error["msg"].removeprefix("Value error, ")
        fields.setdefault(key, message)
    return fields


def _manifest_error(exc: ValidationError) -> ManifestError:
    key, message = next(iter(manifest_field_errors(exc).items()))
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
            snapshots=raw.workspace.snapshots,
        ),
        memory=Memory(recap=raw.memory.recap),
        feedback=Feedback(ask=raw.feedback.ask),
        tools=Tools(
            defaults=raw.tools.defaults,
            bash_timeout_seconds=raw.tools.bash_timeout_seconds,
        ),
        routines=Routines(timezone=ZoneInfo(raw.routines.timezone)),
        serve=raw.serve.listen if raw.serve is not None else None,
        budgets=Budgets(
            steps=raw.budgets.steps,
            tokens=raw.budgets.tokens,
            seconds=raw.budgets.seconds,
            usd_per_day=raw.budgets.usd_per_day,
        ),
        prices={
            model: ModelPrice(
                input=price.input,
                output=price.output,
                cache_read=price.cache_read,
                cache_write=price.cache_write,
            )
            for model, price in raw.prices.items()
        },
        package=(
            PackageProvenance(
                id=raw.package.id,
                distribution=raw.package.distribution,
                version=raw.package.version,
            )
            if raw.package is not None
            else None
        ),
    )


def inspect_instance(
    directory: Path,
    *,
    matching_rule: MatchingRule = "explicit directory",
    model_override: str | None = None,
) -> Instance:
    """Read validated management metadata without loading instance secrets."""
    instance_path = Path(directory).resolve()
    manifest_path = instance_path / MANIFEST_NAME
    try:
        raw = parse_manifest(manifest_path.read_text(encoding="utf-8"))
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


def load_instance(
    directory: Path,
    *,
    matching_rule: MatchingRule = "explicit directory",
    model_override: str | None = None,
) -> Instance:
    """Load one instance for boot, including its non-overriding environment."""
    instance_path = Path(directory).resolve()
    load_dotenv(instance_path / ENV_NAME, override=False)
    return inspect_instance(
        instance_path,
        matching_rule=matching_rule,
        model_override=model_override,
    )


def reload_manifest(instance: Instance, *, model_override: str | None = None) -> Manifest:
    return load_instance(
        instance.path,
        matching_rule=instance.matching_rule,
        model_override=model_override,
    ).manifest
