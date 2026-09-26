"""Check the values a client sends for the setup fields a prepared image declares."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from kinby.contracts import PackageDescription, SetupFieldKind, SetupValue
from kinby.instance import ModelName
from kinby.packages import MODEL_FIELD, package_fields, resolved_values, value_problem

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODEL_NAME = TypeAdapter(ModelName)


def setup_errors(
    description: PackageDescription,
    *,
    model: str,
    config: Mapping[str, SetupValue],
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
    secret_fields = [
        field for field in description.setup_fields if field.kind is SetupFieldKind.SECRET
    ]
    values = configuration(description, model=model, config=config) | resolved_values(
        secret_fields, secrets
    )
    for field in description.setup_fields:
        if field.name in errors:
            continue
        value = values.get(field.name)
        if value is None:
            if field.required:
                errors[field.name] = f"{field.label} is required."
        elif (problem := value_problem(field, value)) is not None:
            errors[field.name] = f"{field.label} {problem}."
    if MODEL_FIELD.name not in errors and not _is_model_name(values.get(MODEL_FIELD.name)):
        errors[MODEL_FIELD.name] = "Name the provider and the model, like openai:gpt-5."
    return errors


def configuration(
    description: PackageDescription,
    *,
    model: str,
    config: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """Each configuration field's value, the model's included, with defaults filling the gaps."""
    fields = [field for field in description.setup_fields if field.kind is SetupFieldKind.CONFIG]
    return resolved_values(fields, {**config, MODEL_FIELD.name: model})


def targeted(
    description: PackageDescription,
    configured: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """The values of the package's own fields, which initialization writes at their targets."""
    return {
        field.name: configured[field.name]
        for field in package_fields(description.setup_fields)
        if field.target is not None and field.name in configured
    }


def api_key_variable(model: ModelName) -> str:
    """Where the model's provider looks for its key, by the `<PROVIDER>_API_KEY` convention."""
    provider, _, _ = model.partition(":")
    return f"{provider.upper()}_API_KEY"


def _is_model_name(value: SetupValue | None) -> bool:
    try:
        _MODEL_NAME.validate_python(value)
    except ValidationError:
        return False
    return True
