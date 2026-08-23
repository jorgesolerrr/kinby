from __future__ import annotations

import os
from pathlib import Path

from kinby.instance.dataclasses import Instance
from kinby.instance.errors import InstanceNotFoundError
from kinby.instance.layout import MANIFEST_NAME
from kinby.instance.manifest import load_instance


def discover_instance(directory: Path | None = None) -> Instance:
    if directory is not None:
        return load_instance(directory)
    environment_directory = os.environ.get("KINBY_INSTANCE")
    if environment_directory:
        return load_instance(
            Path(environment_directory),
            matching_rule="KINBY_INSTANCE",
        )
    current_directory = Path.cwd()
    for candidate in (current_directory, *current_directory.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return load_instance(candidate, matching_rule="walk-up")
    home_default = Path.home() / ".kinby" / "default"
    if (home_default / MANIFEST_NAME).is_file():
        return load_instance(home_default, matching_rule="home default")
    raise InstanceNotFoundError("No kinby instance found. Run `kinby init <directory>` first.")
