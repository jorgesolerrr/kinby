"""Every tool's definition, as the model binding sends it on each call, stays small."""

import json

import pytest
from langchain_anthropic.chat_models import convert_to_anthropic_tool

import kinby.core  # noqa: F401  kinby.plugins.core needs kinby.core imported first.
from kinby.contracts import Scope
from kinby.instance import init_instance, inspect_instance
from kinby.plugins import defaults
from kinby.plugins.core import core_tools
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, HubWriteOperation, hub_write
from kinby.plugins.tools import Tool
from kinby.plugins.views import view_patch, view_write

#: The most characters one tool's definition takes, as compact JSON.
TOOL_BUDGET = 2_000
#: The most characters a home user turn's whole tool list takes, as compact JSON.
HOME_BUDGET = 9_000


def definition(tool: Tool) -> str:
    return json.dumps(convert_to_anthropic_tool(tool.runnable), separators=(",", ":"))


def test_the_tools_of_a_home_user_turn_fit_the_budgets(tmp_path, monkeypatch):
    monkeypatch.setenv(HUB_URL_VARIABLE, "ws://hub/instances/home/hub")
    monkeypatch.setenv(HUB_SCOPES_VARIABLE, Scope.HUB_HOME)
    directory = init_instance(tmp_path / "home")
    tools = (*core_tools(inspect_instance(directory), ()), *defaults.TOOLS)

    sizes = {tool.name: len(definition(tool)) for tool in tools}

    assert "hub_write" in sizes
    assert {name: size for name, size in sizes.items() if size > TOOL_BUDGET} == {}
    assert sum(sizes.values()) <= HOME_BUDGET


@pytest.mark.parametrize("tool", [view_write, view_patch], ids=lambda tool: tool.name)
def test_the_view_writes_leave_the_view_catalogue_to_its_skill(tool):
    sent = definition(tool)

    assert len(sent) < 1_000
    assert "Kanban" not in sent
    assert "write-view" in sent


def test_hub_write_names_every_operation():
    described = hub_write.runnable.description

    assert [operation for operation in HubWriteOperation if operation not in described] == []
