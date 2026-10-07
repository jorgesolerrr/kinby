"""Generate the JSON Schema for a factory file, for editors and the home instance."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue

from kinby.checkout import checkout_path
from kinby.factories.file import FACTORY_FILE_ADAPTER


def checkout_schema_path() -> Path:
    """The generated schema file in this repo."""
    return checkout_path("docs/schema/factory.schema.json")


def factory_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema the factory file's declarations make."""
    schema: dict[str, JsonValue] = FACTORY_FILE_ADAPTER.json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def main(schema_path: Path | None = None) -> None:
    """Write the factory schema to *schema_path*."""
    path = checkout_schema_path() if schema_path is None else schema_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{json.dumps(factory_schema(), indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
