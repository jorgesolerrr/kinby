"""Write the contract's JSON Schema for clients written outside Python.

The web app generates its TypeScript types from the file this writes.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from kinby.checkout import checkout_path
from kinby.contracts.frames import ClientFrame, ServerFrame
from kinby.contracts.methods import METHODS, SUBSCRIPTIONS


class ClientSchema(GenerateJsonSchema):
    """Shape each model the way a client's type generator reads it."""

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        """Keep a ``const`` field required, and drop field titles.

        A client discriminates a union on the ``const`` field. The generator declares one type per
        titled field, and the field's name already names it.
        """
        generated = super().model_schema(schema)
        properties = generated.get("properties", {})
        for field in properties.values():
            field.pop("title", None)
        required = generated.get("required", [])
        missing = [
            name for name, field in properties.items() if "const" in field and name not in required
        ]
        if missing:
            generated["required"] = [*required, *missing]
        return generated


def checkout_schema_path() -> Path:
    """The generated schema file in this repo."""
    return checkout_path("docs/schema/contract.schema.json")


def contract_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema of every frame, and of each method's command and answer."""
    frames = {"ClientFrame": ClientFrame, "ServerFrame": ServerFrame}
    models = {
        model.__name__: model for method in METHODS for model in (method.command, method.result)
    } | {
        model.__name__: model
        for subscription in SUBSCRIPTIONS
        for model in (subscription.command, subscription.item)
    }
    references, definitions = ClientSchema(ref_template="#/$defs/{model}").generate_definitions(
        [
            (name, "validation", TypeAdapter(value).core_schema)
            for name, value in (frames | models).items()
        ]
    )

    def reference(name: str) -> JsonSchemaValue:
        return references[name, "validation"]

    contract = _closed_object(
        {
            "client_frame": {"$ref": "#/$defs/ClientFrame"},
            "server_frame": {"$ref": "#/$defs/ServerFrame"},
            "methods": _closed_object(
                {
                    method.name: _closed_object(
                        {
                            "command": reference(method.command.__name__),
                            "result": reference(method.result.__name__),
                        }
                    )
                    for method in METHODS
                }
            ),
            "subscriptions": _closed_object(
                {
                    subscription.name: _closed_object(
                        {
                            "command": reference(subscription.command.__name__),
                            "item": reference(subscription.item.__name__),
                        }
                    )
                    for subscription in SUBSCRIPTIONS
                }
            ),
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Contract",
        **contract,
        "$defs": {**definitions, **{name: reference(name) for name in frames}},
    }


def _closed_object(properties: dict[str, JsonSchemaValue]) -> JsonSchemaValue:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def main(schema_path: Path | None = None) -> None:
    """Write the contract schema to *schema_path*."""
    path = checkout_schema_path() if schema_path is None else schema_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{json.dumps(contract_schema(), indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
