"""Describe the wire frames for clients written outside Python."""

from __future__ import annotations

from pydantic import JsonValue, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from kinby.contracts.frames import Frame


class RequiredDiscriminants(GenerateJsonSchema):
    """Keep a ``const`` field required: a client discriminates a frame on it."""

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        generated = super().model_schema(schema)
        required = generated.get("required", [])
        constants = [
            name for name, field in generated.get("properties", {}).items() if "const" in field
        ]
        missing = [name for name in constants if name not in required]
        if missing:
            generated["required"] = [*required, *missing]
        return generated


def frames_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema for every frame the contract sends over a socket."""
    schema: dict[str, JsonValue] = TypeAdapter(Frame).json_schema(
        schema_generator=RequiredDiscriminants
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "kinby contract frames"
    return schema
