import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from kinby.contracts import methods
from kinby.contracts.schema import checkout_schema_path, contract_schema, main


def _object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def test_checked_in_contract_schema_is_current() -> None:
    checked_in_schema = json.loads(checkout_schema_path().read_text(encoding="utf-8"))

    assert checked_in_schema == contract_schema(), (
        "regenerate it with `uv run python -m kinby.contracts.schema`"
    )


def test_schema_module_rewrites_the_schema_file(tmp_path: Path) -> None:
    schema_path = tmp_path / "docs" / "schema" / "contract.schema.json"
    schema_path.parent.mkdir(parents=True)
    schema_path.write_text('{"stale": true}\n', encoding="utf-8")

    main(schema_path)

    assert json.loads(schema_path.read_text(encoding="utf-8")) == contract_schema()


def test_the_schema_names_every_frame_a_client_and_the_server_send() -> None:
    definitions = _object(contract_schema()["$defs"])

    assert _object(definitions["ClientFrame"])["oneOf"] == [
        {"$ref": "#/$defs/CallFrame"},
        {"$ref": "#/$defs/SubscribeFrame"},
        {"$ref": "#/$defs/CancelFrame"},
    ]
    assert _object(definitions["ServerFrame"])["oneOf"] == [
        {"$ref": "#/$defs/ResultFrame"},
        {"$ref": "#/$defs/ErrorFrame"},
        {"$ref": "#/$defs/SubscribedFrame"},
        {"$ref": "#/$defs/ItemFrame"},
        {"$ref": "#/$defs/EndFrame"},
    ]


def test_the_schema_ties_every_method_name_to_its_command_and_result() -> None:
    schema = contract_schema()
    declared = vars(methods).values()
    method_schemas = _object(_object(_object(schema["properties"])["methods"])["properties"])
    subscription_schemas = _object(
        _object(_object(schema["properties"])["subscriptions"])["properties"]
    )

    assert set(method_schemas) == {
        value.name for value in declared if isinstance(value, methods.Method)
    }
    assert set(subscription_schemas) == {
        value.name for value in declared if isinstance(value, methods.Subscription)
    }
    assert _object(method_schemas["instance.list"])["properties"] == {
        "command": {"$ref": "#/$defs/InstanceListCommand"},
        "result": {"$ref": "#/$defs/InstanceListResult"},
    }
    assert _object(subscription_schemas["thread.subscribe"])["properties"] == {
        "command": {"$ref": "#/$defs/ThreadSubscribeCommand"},
        "item": {"$ref": "#/$defs/Event"},
    }


def test_every_const_discriminant_is_required_so_a_union_narrows_on_it() -> None:
    definitions = _object(contract_schema()["$defs"])
    discriminated = 0

    for definition in definitions.values():
        properties = _object(definition).get("properties", {})
        required = _object(definition).get("required", [])
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        for name, field in properties.items():
            if "const" in _object(field):
                discriminated += 1
                assert name in required

    assert discriminated > 0


def test_a_thread_summary_carries_its_status_and_last_activity() -> None:
    definitions = _object(contract_schema()["$defs"])
    summary = _object(definitions["ThreadSummary"])

    assert {"status", "last_activity_at"} <= set(_object(summary["properties"]))
    assert {"status", "last_activity_at"} <= set(cast(list[str], summary["required"]))
    assert _object(definitions["ThreadStatus"])["enum"] == [
        "idle",
        "running",
        "awaiting_approval",
        "failed",
    ]


def test_a_thread_summary_carries_its_mode_and_the_list_its_ceiling() -> None:
    definitions = _object(contract_schema()["$defs"])
    summary = _object(definitions["ThreadSummary"])
    listed = _object(definitions["ThreadListResult"])

    assert {"mode", "mode_pinned"} <= set(cast(list[str], summary["required"]))
    assert "ceiling" in cast(list[str], listed["required"])


def test_the_schema_declares_thread_rename() -> None:
    schema = contract_schema()
    methods_schema = _object(_object(_object(schema["properties"])["methods"])["properties"])
    rename = _object(_object(methods_schema["thread.rename"])["properties"])
    title = _object(_object(_object(schema["$defs"])["ThreadRenameCommand"])["properties"])["title"]

    assert rename["result"] == {"$ref": "#/$defs/ThreadSummary"}
    assert {"minLength": 1, "maxLength": 200}.items() <= _object(title).items()


def test_an_approval_answer_is_a_decision_with_an_optional_reason() -> None:
    definitions = _object(contract_schema()["$defs"])
    respond = _object(definitions["ThreadApprovalRespondCommand"])
    gated = _object(definitions["ToolGated"])

    assert set(_object(respond["properties"])) == {"thread_id", "approval_id", "decision", "reason"}
    assert "decision" in cast(list[str], respond["required"])
    assert _object(definitions["ApprovalDecision"])["enum"] == ["approve", "deny"]
    assert "reason" in _object(gated["properties"])


def test_the_schema_declares_the_prompt_and_config_history_calls() -> None:
    schema = contract_schema()
    definitions = _object(schema["$defs"])
    methods_schema = _object(_object(_object(schema["properties"])["methods"])["properties"])

    assert {
        name: _object(methods_schema[name])["properties"]
        for name in ("prompt.get", "prompt.set", "config.history")
    } == {
        "prompt.get": {
            "command": {"$ref": "#/$defs/PromptGetCommand"},
            "result": {"$ref": "#/$defs/PromptResult"},
        },
        "prompt.set": {
            "command": {"$ref": "#/$defs/PromptSetCommand"},
            "result": {"$ref": "#/$defs/PromptResult"},
        },
        "config.history": {
            "command": {"$ref": "#/$defs/ConfigHistoryCommand"},
            "result": {"$ref": "#/$defs/ConfigHistoryResult"},
        },
    }
    assert set(cast(list[str], _object(definitions["PromptResult"])["required"])) == {
        "content",
        "hash",
        "default",
    }
    assert set(cast(list[str], _object(definitions["ConfigChange"])["required"])) == {
        "at",
        "file",
        "actor",
        "diff",
    }
    assert _object(definitions["ConfigActor"])["enum"] == ["app", "agent", "failure_policy"]
    assert "STALE" in cast(list[str], _object(definitions["ErrorCode"])["enum"])
