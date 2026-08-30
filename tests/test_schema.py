import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from kinby.instance.schema import checkout_schema_path, main, manifest_schema

EXAMPLE_INSTANCES = Path(__file__).parents[1] / "examples" / "instances"


def test_checked_in_manifest_schema_is_current() -> None:
    checked_in_schema = json.loads(checkout_schema_path().read_text(encoding="utf-8"))

    assert checked_in_schema == manifest_schema()


def test_checkout_schema_path_is_the_checked_in_file() -> None:
    assert (
        checkout_schema_path()
        == Path(__file__).resolve().parents[1] / "docs" / "schema" / "kinby.schema.json"
    )


def test_schema_module_rewrites_the_schema_file(tmp_path: Path) -> None:
    schema_path = tmp_path / "docs" / "schema" / "kinby.schema.json"
    schema_path.parent.mkdir(parents=True)
    schema_path.write_text('{"stale": true}\n', encoding="utf-8")

    main(schema_path)

    assert json.loads(schema_path.read_text(encoding="utf-8")) == manifest_schema()


@pytest.mark.parametrize(
    "manifest_path",
    sorted(EXAMPLE_INSTANCES.glob("*/kinby.toml")),
    ids=lambda path: path.parent.name,
)
def test_example_manifest_validates_against_the_schema(manifest_path: Path) -> None:
    with manifest_path.open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)

    validate(instance=manifest, schema=manifest_schema())


@pytest.mark.parametrize("model", ["gpt-5", "openai:gpt-5\n"])
def test_manifest_schema_rejects_an_invalid_model(model: str) -> None:
    manifest = {"id": "alice", "models": {"main": model}}

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


def test_manifest_schema_accepts_the_tools_table() -> None:
    manifest = {
        "id": "locked-down",
        "models": {"main": "openai:gpt-5"},
        "tools": {"defaults": False},
    }

    validate(instance=manifest, schema=manifest_schema())
