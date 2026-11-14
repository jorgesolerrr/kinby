import json
from pathlib import Path

import pytest
import yaml
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from kinby.factories.schema import checkout_schema_path, factory_schema, main
from tests.test_hub_factories import FACTORY
from tests.test_hub_workbench_factories import FACTORY as REQUIRING_FACTORY


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


def test_the_schema_refuses_a_factory_that_declares_instances() -> None:
    with pytest.raises(ValidationError):
        validate(instance=yaml.safe_load(FACTORY), schema=factory_schema())


def test_the_schema_accepts_a_factory_that_requires_a_workbench() -> None:
    validate(instance=yaml.safe_load(REQUIRING_FACTORY), schema=factory_schema())


def test_the_schema_refuses_a_step_that_names_an_instance_in_a_requiring_factory() -> None:
    factory = yaml.safe_load(REQUIRING_FACTORY)
    factory["steps"][0]["in"] = "coder"

    with pytest.raises(ValidationError):
        validate(instance=factory, schema=factory_schema())


def test_the_schema_accepts_a_list_name_in_a_command_steps_run() -> None:
    factory = yaml.safe_load(REQUIRING_FACTORY)
    [step] = [step for step in factory["steps"] if step["kind"] == "command"]
    step["run"] = ["git status", {"commands": "setup"}]

    validate(instance=factory, schema=factory_schema())
    for malformed in ("", {"commands": "Set Up"}, {"commands": "setup", "run": ["make"]}):
        step["run"] = [malformed]
        with pytest.raises(ValidationError):
            validate(instance=factory, schema=factory_schema())
