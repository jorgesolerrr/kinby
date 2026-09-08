import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from kinby.instance import ManifestError, ModelPrice, init_instance, load_instance
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


def test_manifest_schema_names_and_accepts_the_budgets_section() -> None:
    schema = manifest_schema()
    manifest = {
        "id": "bounded",
        "models": {"main": "openai:gpt-5"},
        "budgets": {
            "steps": 7,
            "tokens": 1000,
            "seconds": 2.5,
            "usd_per_day": 1.25,
        },
    }

    properties = schema["properties"]
    assert isinstance(properties, dict)
    budgets = properties["budgets"]
    assert isinstance(budgets, dict)
    assert budgets["title"] == "Budgets"
    validate(instance=manifest, schema=schema)


@pytest.mark.parametrize("value", [0, -1])
def test_manifest_schema_rejects_a_non_positive_budget(value: int) -> None:
    manifest = {
        "id": "bounded",
        "models": {"main": "openai:gpt-5"},
        "budgets": {"steps": value},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


@pytest.mark.parametrize("policy", ["every-turn", "off"])
def test_manifest_schema_accepts_recap_policies(policy: str) -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "memory": {"recap": policy},
    }

    validate(instance=manifest, schema=manifest_schema())


def test_manifest_schema_rejects_an_unknown_recap_policy() -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "memory": {"recap": "sometimes"},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


@pytest.mark.parametrize("policy", ["every-turn", "off"])
def test_manifest_schema_accepts_feedback_policies(policy: str) -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "feedback": {"ask": policy},
    }

    validate(instance=manifest, schema=manifest_schema())


def test_manifest_schema_accepts_the_serve_table() -> None:
    schema = manifest_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert "serve" in properties
    validate(
        instance={
            "id": "alice",
            "models": {"main": "openai:gpt-5"},
            "serve": {"listen": "127.0.0.1:8484"},
        },
        schema=schema,
    )


@pytest.mark.parametrize(
    "listen",
    ["not-an-address", "bad host:8484", "http://localhost:8484", "localhost:+8484"],
)
def test_manifest_schema_rejects_a_malformed_listen_address(listen: str) -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "serve": {"listen": listen},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


def test_manifest_schema_rejects_an_unknown_feedback_key() -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "feedback": {"sometimes": True},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


def test_manifest_schema_names_and_accepts_the_prices_section() -> None:
    schema = manifest_schema()
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "prices": {"openai:gpt-5": {"input": 2, "output": 4}},
    }

    properties = schema["properties"]
    assert isinstance(properties, dict)
    prices = properties["prices"]
    assert isinstance(prices, dict)
    assert prices["title"] == "Prices"
    validate(instance=manifest, schema=schema)


def test_manifest_schema_lists_and_accepts_cache_prices() -> None:
    schema = manifest_schema()
    defs = schema["$defs"]
    assert isinstance(defs, dict)
    raw_price = defs["RawModelPrice"]
    assert isinstance(raw_price, dict)
    properties = raw_price["properties"]
    assert isinstance(properties, dict)
    assert "cache_read" in properties
    assert "cache_write" in properties
    validate(
        instance={
            "id": "alice",
            "models": {"main": "openai:gpt-5"},
            "prices": {
                "openai:gpt-5": {
                    "input": 1.25,
                    "output": 10,
                    "cache_read": 0.125,
                    "cache_write": 1.5625,
                }
            },
        },
        schema=schema,
    )


def test_manifest_loads_cache_prices(tmp_path: Path) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path, model="openai:gpt-5")
    with (instance_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(
            '\n[prices."openai:gpt-5"]\n'
            "input = 1.25\n"
            "output = 10\n"
            "cache_read = 0.125\n"
            "cache_write = 1.5625\n"
        )

    instance = load_instance(instance_path)

    assert instance.manifest.prices["openai:gpt-5"] == ModelPrice(
        input=1.25,
        output=10,
        cache_read=0.125,
        cache_write=1.5625,
    )


@pytest.mark.parametrize("field", ["cache_read", "cache_write"])
def test_manifest_rejects_non_finite_cache_prices(tmp_path: Path, field: str) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path, model="openai:gpt-5")
    with (instance_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(f'\n[prices."openai:gpt-5"]\ninput = 1\noutput = 1\n{field} = inf\n')

    with pytest.raises(ManifestError, match=rf"prices\.openai:gpt-5\.{field}.*finite"):
        load_instance(instance_path)


def test_manifest_schema_rejects_an_invalid_price_model_name() -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "prices": {"not-a-model": {"input": 2, "output": 4}},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())


@pytest.mark.parametrize(
    "price",
    [
        {"input": "one", "output": 4},
        {"input": 2, "output": 4, "cached": 0.2},
    ],
)
def test_manifest_schema_rejects_invalid_model_prices(price: dict[str, object]) -> None:
    manifest = {
        "id": "alice",
        "models": {"main": "openai:gpt-5"},
        "prices": {"openai:gpt-5": price},
    }

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=manifest_schema())
