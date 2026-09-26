"""Check the values a client sends for the setup fields a prepared image declares."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from kinby.contracts import PackageDescription, SetupFieldKind
from kinby.instance import ModelName
from kinby.packages import MODEL_FIELD

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODEL_NAME = TypeAdapter(ModelName)


def setup_errors(
    description: PackageDescription,
    *,
    model: str,
    config: Mapping[str, str],
    secrets: Mapping[str, str],
) -> dict[str, str]:
    """What is wrong with each value, by field name. Empty when the image takes them all.

    A secret the image does not declare is still an environment variable the instance may hold.
    A configuration value it does not declare has nowhere to land. The model travels as the
    command's own `model`, so it is never a configuration value.
    """
    errors: dict[str, str] = {}
    declared = {
        field.name for field in description.setup_fields if field.kind is SetupFieldKind.CONFIG
    }
    for name in config:
        if name == MODEL_FIELD.name:
            errors[name] = "Send the model as the command's model."
        elif name not in declared:
            errors[name] = "This image declares no configuration field by this name."
    for name in secrets:
        if ENVIRONMENT_NAME.fullmatch(name) is None:
            errors[name] = "A secret's name must be an environment variable name."
    for field in description.setup_fields:
        values = config if field.kind is SetupFieldKind.CONFIG else secrets
        value = model if field.name == MODEL_FIELD.name else values.get(field.name, "")
        if field.required and not value.strip():
            errors[field.name] = f"{field.label} is required."
    if MODEL_FIELD.name not in errors and not _is_model_name(model):
        errors[MODEL_FIELD.name] = "Name the provider and the model, like openai:gpt-5."
    return errors


def api_key_variable(model: ModelName) -> str:
    """Where the model's provider looks for its key, by the `<PROVIDER>_API_KEY` convention."""
    provider, _, _ = model.partition(":")
    return f"{provider.upper()}_API_KEY"


def _is_model_name(value: str) -> bool:
    try:
        _MODEL_NAME.validate_python(value)
    except ValidationError:
        return False
    return True
