import json
from pathlib import Path

import pytest
import yaml
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from kinby.factories.schema import checkout_schema_path, factory_schema, main
from tests.test_hub_factories import FACTORY


def test_checked_in_factory_schema_is_current() -> None:
    checked_in_schema = json.loads(checkout_schema_path().read_text(encoding="utf-8"))

    assert checked_in_schema == factory_schema(), (
        "regenerate it with `uv run python -m kinby.factories.schema`"
    )


def test_schema_module_rewrites_the_schema_file(tmp_path: Path) -> None:
    schema_path = tmp_path / "factory.schema.json"
    schema_path.write_text('{"stale": true}\n', encoding="utf-8")

    main(schema_path)

    assert json.loads(schema_path.read_text(encoding="utf-8")) == factory_schema()


def test_the_schema_accepts_a_factory_file() -> None:
    validate(instance=yaml.safe_load(FACTORY), schema=factory_schema())


@pytest.mark.parametrize("kind", ["agent", "client"])
def test_the_schema_requires_a_hook_on_agent_and_client_steps(kind: str) -> None:
    factory = yaml.safe_load(FACTORY)
    [step] = [step for step in factory["steps"] if step["kind"] == kind]
    del step["hook"]

    with pytest.raises(ValidationError) as raised:
        validate(instance=factory, schema=factory_schema())

    # A step is one of the step kinds, so the missing hook is what fails the kind it names.
    assert "'hook' is a required property" in [error.message for error in raised.value.context]
