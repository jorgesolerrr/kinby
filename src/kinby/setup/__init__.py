"""The setup fields every instance asks for, and the values and template files they read."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from kinby.contracts import (
    DeclaredSetup,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
)

_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+(\.[^@\s.]+)+")

MODEL_FIELD = SetupField(
    name="model",
    label="Model",
    description="The model the instance calls, as provider:model, like openai:gpt-5.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.TEXT,
    required=True,
)
API_KEY_FIELD = SetupField(
    name="api_key",
    label="API key",
    description="The key your model provider issued. The hub keeps it with the instance's "
    "secrets and never shows it again.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=True,
)
#: What every instance asks for, whatever it starts from. kinby writes these values itself.
BUILT_IN_FIELDS = (MODEL_FIELD, API_KEY_FIELD)
#: Only a vanilla instance asks for it. A template keeps the behavior prompt it ships.
BEHAVIOR_PROMPT_FIELD = SetupField(
    name="behavior_prompt",
    label="Behavior prompt",
    description="Instructions the instance follows in every turn. Leave it empty to keep "
    "kinby's default.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.MULTILINE,
    required=False,
)


def vanilla_description() -> DeclaredSetup:
    """What kinby's base image declares: the built-in fields and the behavior prompt."""
    return DeclaredSetup(setup_fields=[*BUILT_IN_FIELDS, BEHAVIOR_PROMPT_FIELD])


def with_built_in_fields(own: Iterable[SetupField]) -> list[SetupField]:
    """The built-in fields, with the defaults *own* gives them, then *own*'s other fields."""
    own = tuple(own)
    overrides = {field.name: field.default for field in own if field.default is not None}
    built_in = [
        field.model_copy(update={"default": overrides[field.name]})
        if field.name in overrides
        else field
        for field in BUILT_IN_FIELDS
    ]
    return [*built_in, *template_fields(own)]


def template_fields(fields: Iterable[SetupField]) -> list[SetupField]:
    """The fields a template adds, leaving out those that override a built-in field's default."""
    built_in = {field.name for field in BUILT_IN_FIELDS}
    return [field for field in fields if field.name not in built_in]


def value_problem(field: SetupField, value: SetupValue) -> str | None:
    """Why *value* does not fit *field*'s type, worded to follow "the value". None when it fits."""
    match field.type:
        case SetupFieldType.TEXT | SetupFieldType.MULTILINE if not isinstance(value, str):
            return "is not text"
        case SetupFieldType.BOOLEAN if not isinstance(value, bool):
            return "is not true or false"
        case SetupFieldType.INTEGER if isinstance(value, bool) or not isinstance(value, int):
            return "is not a whole number"
        case SetupFieldType.CHOICE if value not in (field.choices or ()):
            return f"is not one of {', '.join(field.choices or ())}"
        case SetupFieldType.EMAIL if not _is_email(value):
            return "is not an email address, like someone@example.com"
        case SetupFieldType.URL if not _is_url(value):
            return "is not a URL with a scheme and a host, like https://example.com"
    return None


def _is_email(value: SetupValue) -> bool:
    """One address: no spaces, and a dot in the domain."""
    return isinstance(value, str) and _EMAIL.fullmatch(value) is not None


def _is_url(value: SetupValue) -> bool:
    """An absolute URL. `owner/name` and the scp form `git@host:owner/name.git` have no scheme."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.hostname)


def resolved_values(
    fields: Iterable[SetupField],
    values: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """Each field's value: the one sent, else its default. Blank text counts as nothing sent."""
    resolved: dict[str, SetupValue] = {}
    for field in fields:
        value = values.get(field.name)
        if value is None or (isinstance(value, str) and not value.strip()):
            value = field.default
        if value is not None:
            resolved[field.name] = value
    return resolved


def readable_template_files(template: Path) -> dict[str, str]:
    """Read the text files in an instance template.

    Bytecode Python left beside a template's tools and hooks is not part of it.
    """
    root = Path(template).resolve()
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.relative_to(root).parts
    }


__all__ = [
    "API_KEY_FIELD",
    "BEHAVIOR_PROMPT_FIELD",
    "BUILT_IN_FIELDS",
    "MODEL_FIELD",
    "readable_template_files",
    "resolved_values",
    "template_fields",
    "value_problem",
    "vanilla_description",
    "with_built_in_fields",
]
