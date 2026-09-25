"""Generate the JSON Schema for ``kinby.toml``."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue

from kinby.checkout import checkout_path
from kinby.instance.manifest import RawManifest


def checkout_schema_path() -> Path:
    """The generated schema file in this repo."""
    return checkout_path("docs/schema/kinby.schema.json")


def manifest_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema declared by the manifest model."""
    schema: dict[str, JsonValue] = RawManifest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def main(schema_path: Path | None = None) -> None:
    """Write the manifest schema to *schema_path*."""
    path = checkout_schema_path() if schema_path is None else schema_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{json.dumps(manifest_schema(), indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
