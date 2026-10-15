"""Check the values a client sends for the setup fields a prepared image declares, and read
how much of that setup an instance has done."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from kinby.contracts import (
    CommandListName,
    DeclaredSetup,
    HubSecretName,
    InstanceSetup,
    LoginSetup,
    LoginState,
    SecretSetup,
    SetupField,
    SetupFieldKind,
    SetupValue,
)
from kinby.instance import ModelName, api_key_variable
from kinby.setup import (
    API_KEY_FIELD,
    MODEL_FIELD,
    resolved_values,
    template_fields,
    value_problem,
)

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODEL_NAME = TypeAdapter(ModelName)


def setup_errors(
    description: DeclaredSetup,
    *,
    model: str,
    config: Mapping[str, SetupValue],
    secrets: Mapping[str, str],
) -> dict[str, str]:
    """What is wrong with each value, by field name. Empty when the image takes them all.

    A secret the image does not declare is still an environment variable the instance may hold.
    A configuration value it does not declare has nowhere to land. The model travels as the
    command's own `model`, so it is never a configuration value. A required secret left empty
    is pending setup, filled after creation, so only a configuration field can be missing.
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
            if field.required and field.kind is SetupFieldKind.CONFIG:
                errors[field.name] = f"{field.label} is required."
        elif (problem := value_problem(field, value)) is not None:
            errors[field.name] = f"{field.label} {problem}."
    if MODEL_FIELD.name not in errors and not _is_model_name(values.get(MODEL_FIELD.name)):
        errors[MODEL_FIELD.name] = "Name the provider and the model, like openai:gpt-5."
    return errors


def link_errors(
    secrets: Mapping[str, str],
    hub_secrets: Mapping[str, HubSecretName],
    save_to_hub: Mapping[str, HubSecretName],
    kept: Collection[HubSecretName],
) -> dict[str, str]:
    """What is wrong with each field's hub secret, or with saving its typed value to the hub, by
    field name. Blank text counts as nothing typed, the way creation reads it.

    A save never replaces a hub secret: replacing one rewrites the instances linked to it
    (ADR 0095), which a creation does not do.
    """
    typed = {name for name, value in secrets.items() if value.strip()}
    saved = Counter(save_to_hub.values())
    errors: dict[str, str] = {}
    for name, hub_secret in save_to_hub.items():
        if name not in typed:
            errors[name] = "Type a value to save it to the hub."
        elif hub_secret in kept:
            errors[name] = f'The hub keeps a secret named "{hub_secret}" already.'
        elif saved[hub_secret] > 1:
            errors[name] = f'Another field saves to "{hub_secret}" too.'
    for name, hub_secret in hub_secrets.items():
        if name in typed:
            errors[name] = "Type a value or take a hub secret, not both."
        elif hub_secret not in kept:
            errors[name] = f'The hub keeps no secret named "{hub_secret}".'
    return errors


def configuration(
    description: DeclaredSetup,
    *,
    model: str,
    config: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """Each configuration field's value, the model's included, with defaults filling the gaps."""
    fields = [field for field in description.setup_fields if field.kind is SetupFieldKind.CONFIG]
    return resolved_values(fields, {**config, MODEL_FIELD.name: model})


def manifest_settings(
    setup: DeclaredSetup,
    configured: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """The values of a template's own fields, by the dotted key of kinby.toml each lands at."""
    return {
        field.target.key: configured[field.name]
        for field in template_fields(setup.setup_fields)
        if field.target is not None and field.name in configured
    }


def held_secrets(model: ModelName, secrets: Mapping[str, str]) -> dict[str, str]:
    """The secrets under the names the instance reads: the API key under its provider's
    variable, every other secret under its own name."""
    return {
        api_key_variable(model) if name == API_KEY_FIELD.name else name: value
        for name, value in secrets.items()
    }


def secret_field(model: ModelName | None, variable: str) -> str:
    """The secret field the instance holds under the variable, the way `held_secrets` names it."""
    if model is not None and variable == api_key_variable(model):
        return API_KEY_FIELD.name
    return variable


def instance_setup(
    description: DeclaredSetup | None,
    *,
    logins: Mapping[str, LoginState],
    running: Mapping[str, UUID],
    secrets: Mapping[str, str],
    links: Mapping[str, HubSecretName],
    model: ModelName | None,
    missing_commands: Sequence[CommandListName],
) -> InstanceSetup:
    """Each login and secret field the instance's stored descriptor declares, and how it stands,
    and the lists of repository commands it is missing.

    `running` names the sign-in each login has running, by login id. `links` names the hub
    secret each linked variable took.

    A login the hub never tracked belongs to an instance created before it tracked logins, and
    reads as signed in. The API key is held under its provider's variable, so it takes the model.
    Blank text counts as not set, the way creation reads it.
    """
    if description is None:
        return InstanceSetup(logins=[], secrets=[], missing_commands=list(missing_commands))

    def variable(field: SetupField) -> str | None:
        if field.name != API_KEY_FIELD.name:
            return field.name
        return None if model is None else api_key_variable(model)

    def is_set(field: SetupField) -> bool:
        held = variable(field)
        return held is not None and bool(secrets.get(held, "").strip())

    def hub_secret(field: SetupField) -> HubSecretName | None:
        held = variable(field)
        return None if held is None else links.get(held)

    return InstanceSetup(
        logins=[
            LoginSetup(
                id=login.id,
                label=login.label,
                description=login.description,
                state=logins.get(login.id, LoginState.SIGNED_IN),
                operation_id=running.get(login.id),
            )
            for login in description.logins
        ],
        secrets=[
            SecretSetup(
                name=field.name,
                variable=variable(field),
                label=field.label,
                required=field.required,
                is_set=is_set(field),
                hub_secret=hub_secret(field),
            )
            for field in description.setup_fields
            if field.kind is SetupFieldKind.SECRET
        ],
        missing_commands=list(missing_commands),
    )


def _is_model_name(value: SetupValue | None) -> bool:
    try:
        _MODEL_NAME.validate_python(value)
    except ValidationError:
        return False
    return True
