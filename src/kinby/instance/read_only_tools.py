"""Keep the authored tools the user marked read-only, which the gate lets run as reads (ADR 0109).

No tool of the agent writes this file.
"""

from pathlib import Path
from threading import Lock

from pydantic import TypeAdapter

from kinby.contracts import ConfigActor, ConfigFile, ToolName
from kinby.instance.code import exposed_tools
from kinby.instance.config_changes import recorded_change
from kinby.instance.dataclasses import Instance
from kinby.instance.layout import READ_ONLY_TOOLS_NAME

_NAMES = TypeAdapter(list[ToolName])
#: A change reads the whole file and writes it back, so changes from any thread go one at a time.
_changing = Lock()


def read_only_tools(instance_path: Path) -> frozenset[str]:
    try:
        return frozenset(_NAMES.validate_json((instance_path / READ_ONLY_TOOLS_NAME).read_bytes()))
    except FileNotFoundError:
        return frozenset()


def set_read_only(instance: Instance, name: str, *, read_only: bool) -> None:
    """Mark or unmark the authored tool *name* read-only, as a config change the user made."""
    if read_only and name not in exposed_tools(instance.path):
        raise LookupError(f'"{name}" is not an authored tool of this instance.')
    with _changing:
        names = read_only_tools(instance.path)
        changed = names | {name} if read_only else names - {name}
        if changed == names:
            return
        path = instance.path / READ_ONLY_TOOLS_NAME
        with recorded_change(instance, ConfigFile(READ_ONLY_TOOLS_NAME), ConfigActor.APP):
            staging = path.with_name(f".{path.name}.staging")
            staging.write_bytes(_NAMES.dump_json(sorted(changed), indent=2))
            staging.replace(path)
