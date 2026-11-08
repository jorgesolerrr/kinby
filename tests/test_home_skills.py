"""The skills kinby ships for the home instance cite only tools, writes and reads that exist."""

import re

import pytest

import kinby.core  # noqa: F401  kinby.plugins.core needs kinby.core imported first.
from kinby.contracts import HUB_READS, RELAYED_READS, Scope
from kinby.contracts.methods import METHODS
from kinby.factories.file import FACTORY_FILE_ADAPTER
from kinby.instance import init_instance, inspect_instance
from kinby.instance.layout import SKILL_FILE
from kinby.library import HOME_SKILLS, SHIPPED_SKILLS
from kinby.plugins import defaults, hub
from kinby.plugins.core import core_tools
from kinby.plugins.hub import (
    HUB_SCOPES_VARIABLE,
    HUB_URL_VARIABLE,
    HubWriteOperation,
    write_arguments,
)
from kinby.plugins.skills import load_skill_file
from kinby.plugins.tools import Tool

_CITED = re.compile(r"`([^`\n]+)`")
_METHOD = re.compile(r"[a-z]+(\.[a-z_]+)+")
_FILE = re.compile(r".+\.(md|yaml|toml|py|json|txt)")
_SNAKE = re.compile(r"[a-z]+(_[a-z]+)+")
_WORD_BREAK = re.compile(r"[\s=]+")
FACTORY_REFERENCE = "references/factory.md"

READS = HUB_READS | RELAYED_READS
READ_FAMILIES = {method.name.split(".")[0] for method in METHODS} | {
    read.split(".")[0] for read in READS
}
OPERATIONS = set(HubWriteOperation)
ARGUMENTS = {name for operation in HubWriteOperation for name in write_arguments(operation)}
OPERATION_FAMILIES = {operation.split("_")[0] for operation in HubWriteOperation}


@pytest.fixture(scope="module")
def home_tools(tmp_path_factory) -> dict[str, list[str]]:
    """Each tool a home instance holds on a turn the user starts, with its parameters."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(HUB_URL_VARIABLE, "ws://hub/instances/home/hub")
        patch.setenv(HUB_SCOPES_VARIABLE, Scope.HUB_HOME)
        directory = init_instance(tmp_path_factory.mktemp("home") / "kinby")
        tools = (*core_tools(inspect_instance(directory), ()), *defaults.TOOLS)
    return {tool.name: list(tool.runnable.args) for tool in tools}


def _text(name: str) -> str:
    return (SHIPPED_SKILLS / name / SKILL_FILE).read_text(encoding="utf-8")


def _cited(name: str) -> set[str]:
    return set(_CITED.findall(_text(name)))


def _words(text: str) -> set[str]:
    """Each word of the text's code spans, a command's name=value split in two."""
    return {word for cited in _CITED.findall(text) for word in _WORD_BREAK.split(cited)}


def unknown_reads(text: str) -> set[str]:
    """The names shaped like a contract method that kinby hub read does not accept."""
    shaped = {
        word
        for word in _words(text)
        if _METHOD.fullmatch(word)
        and not _FILE.fullmatch(word)
        and word.split(".")[0] in READ_FAMILIES
    }
    return shaped - READS


def unknown_operations(text: str) -> set[str]:
    """The names shaped like a hub write that are neither a hub_write operation nor one of an
    operation's arguments."""
    shaped = {
        word
        for word in _words(text)
        if _SNAKE.fullmatch(word) and word.split("_")[0] in OPERATION_FAMILIES
    }
    return shaped - OPERATIONS - ARGUMENTS


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_loads_under_its_folders_name(name):
    assert load_skill_file(SHIPPED_SKILLS / name / SKILL_FILE).name == name


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_only_reads_kinby_hub_read_accepts(name):
    assert unknown_reads(_text(name)) == set()


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_only_hub_write_operations(name):
    assert unknown_operations(_text(name)) == set()


def test_a_made_up_read_or_operation_is_unknown():
    text = "Run `kinby hub read instance.weather instance_id=<id>`, then `instance_explode`."

    assert unknown_reads(text) == {"instance.weather"}
    assert unknown_operations(text) == {"instance_explode"}


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_only_tools_the_home_instance_holds(name, home_tools):
    """A name shaped like one of the home instance's tools is that tool, or one's parameter."""
    families = {tool.split("_")[0] for tool in home_tools if "_" in tool}
    known = (
        home_tools.keys()
        | {parameter for each in home_tools.values() for parameter in each}
        | ARGUMENTS
    )
    shaped = {
        word
        for word in _words(_text(name))
        if _SNAKE.fullmatch(word) and word.split("_")[0] in families
    }

    assert shaped <= known


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_no_other_skill_of_the_library(name):
    shipped = {folder.name for folder in SHIPPED_SKILLS.iterdir() if folder.is_dir()}

    assert not _cited(name) & (shipped - {name})


def test_the_factory_reference_names_every_factory_yaml_field():
    """A new field of factory.yaml fails this until the factory reference names it."""
    schema = FACTORY_FILE_ADAPTER.json_schema(by_alias=True)
    fields = {
        field
        for declaration in (schema, *schema["$defs"].values())
        for field in declaration.get("properties", {})
    }
    reference = SHIPPED_SKILLS / "write-factory" / FACTORY_REFERENCE

    assert fields <= set(_CITED.findall(reference.read_text(encoding="utf-8")))
    assert FACTORY_REFERENCE in _cited("write-factory")


def test_write_routine_names_every_hub_tool_a_routine_lacks():
    """A new hub tool fails this until write-routine says a routine's turns lack it."""
    declared = {each.name for each in vars(hub).values() if isinstance(each, Tool) and each.hub}

    assert declared <= _cited("write-routine")
