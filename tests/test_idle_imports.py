"""Idle memory pays only for what an instance or the hub uses: each entry starts in a fresh
process, which reports the libraries it loaded."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kinby.instance.init import init_instance

_LOADED = "print(json.dumps(sorted({name.partition('.')[0] for name in sys.modules})))"

_INSTANCE = f"""
import asyncio, json, sys
from pathlib import Path

import kinby.cli.main
from kinby.core.models import init_model
from kinby.core.runtime import boot_instance
from kinby.instance import load_instance


async def start():
    instance = load_instance(Path(sys.argv[1]))
    runtime = await boot_instance(instance)
    init_model(instance.manifest.models.main)
    await runtime.stop_after_running_routine()


asyncio.run(start())
{_LOADED}
"""

_HUB = f"""
import json, sys

import kinby.cli.main
import kinby.hub

{_LOADED}
"""

_UNUSED_BY_AN_INSTANCE = {"mcp", "trafilatura", "playwright"}
_UNUSED_BY_THE_HUB = {"langchain", "langgraph", "mcp", "playwright", "trafilatura"}


def _loaded(script: str, *arguments: str) -> set[str]:
    ran = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "ANTHROPIC_API_KEY": "unused", "OPENAI_API_KEY": "unused"},
    )
    assert ran.returncode == 0, ran.stderr
    return set(json.loads(ran.stdout.splitlines()[-1]))


@pytest.mark.parametrize(
    ("model", "other_provider"),
    [
        ("anthropic:claude-sonnet-4-5", {"openai", "langchain_openai"}),
        ("openai:gpt-5", {"anthropic", "langchain_anthropic"}),
    ],
)
def test_an_instance_loads_only_its_own_provider_and_no_unused_library(
    tmp_path: Path, model: str, other_provider: set[str]
) -> None:
    instance = init_instance(tmp_path / "instance", model)

    loaded = _loaded(_INSTANCE, str(instance))

    assert loaded & (other_provider | _UNUSED_BY_AN_INSTANCE) == set()


def test_the_hub_loads_no_instance_core_and_no_browser_library() -> None:
    loaded = _loaded(_HUB)

    assert "docker" in loaded
    assert loaded & _UNUSED_BY_THE_HUB == set()
