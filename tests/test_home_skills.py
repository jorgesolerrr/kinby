"""The skills kinby ships for the home instance cite only tools and reads that exist."""

import re

import pytest

import kinby.core  # noqa: F401  kinby.plugins.core needs kinby.core imported first.
from kinby.contracts import RELAYED_READS, Scope
from kinby.contracts.methods import METHODS
from kinby.instance import init_instance, inspect_instance
from kinby.instance.layout import SKILL_FILE
from kinby.library import HOME_SKILLS, SHIPPED_SKILLS
from kinby.plugins import defaults, hub
from kinby.plugins.core import core_tools
from kinby.plugins.hub import HUB_URL_VARIABLE
from kinby.plugins.skills import load_skill_file
from kinby.plugins.tools import Tool

_CITED = re.compile(r"`([^`\n]+)`")
_METHOD = re.compile(r"[a-z]+(\.[a-z_]+)+")
_FILE = re.compile(r".+\.(md|yaml|toml|py|json|txt)")
_SNAKE = re.compile(r"[a-z]+(_[a-z]+)+")

READS = {method.name for method in METHODS if method.scope is Scope.HUB_READ} | RELAYED_READS


@pytest.fixture(scope="module")
def home_tools(tmp_path_factory) -> dict[str, list[str]]:
    """Each tool a home instance holds on a turn the user starts, with its parameters."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(HUB_URL_VARIABLE, "ws://hub/instances/home/hub")
        directory = init_instance(tmp_path_factory.mktemp("home") / "kinby")
        tools = (*core_tools(inspect_instance(directory), ()), *defaults.TOOLS)
    return {tool.name: list(tool.runnable.args) for tool in tools}


def _cited(name: str) -> set[str]:
    return set(_CITED.findall((SHIPPED_SKILLS / name / SKILL_FILE).read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_loads_under_its_folders_name(name):
    assert load_skill_file(SHIPPED_SKILLS / name / SKILL_FILE).name == name


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_only_methods_hub_read_reads(name):
    """A name shaped like a contract method is one that hub_read reads."""
    families = {method.name.split(".")[0] for method in METHODS} | {
        read.split(".")[0] for read in READS
    }
    shaped = {
        cited
        for cited in _cited(name)
        if _METHOD.fullmatch(cited)
        and not _FILE.fullmatch(cited)
        and cited.split(".")[0] in families
    }

    assert shaped <= READS


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_only_tools_the_home_instance_holds(name, home_tools):
    """A name shaped like one of the home instance's tools is that tool, or one's parameter."""
    families = {tool.split("_")[0] for tool in home_tools if "_" in tool}
    known = home_tools.keys() | {parameter for each in home_tools.values() for parameter in each}
    shaped = {
        cited
        for cited in _cited(name)
        if _SNAKE.fullmatch(cited) and cited.split("_")[0] in families
    }

    assert shaped <= known


@pytest.mark.parametrize("name", HOME_SKILLS)
def test_a_home_skill_names_no_other_skill_of_the_library(name):
    shipped = {folder.name for folder in SHIPPED_SKILLS.iterdir() if folder.is_dir()}

    assert not _cited(name) & (shipped - {name})


def test_write_routine_names_every_hub_tool_a_routine_lacks():
    """A new hub tool fails this until write-routine says a routine's turns lack it."""
    declared = {each.name for each in vars(hub).values() if isinstance(each, Tool) and each.hub}

    assert declared <= _cited("write-routine")
