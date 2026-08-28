"""Find the instance to run when the caller did not name one."""

from __future__ import annotations

import os
from pathlib import Path

from kinby.instance.dataclasses import Instance
from kinby.instance.errors import InstanceNotFoundError
from kinby.instance.layout import MANIFEST_NAME
from kinby.instance.manifest import load_instance


def discover_instance(*, model_override: str | None = None) -> Instance:
    """Search KINBY_INSTANCE, then the directories above cwd, then the home default."""
    environment_directory = os.environ.get("KINBY_INSTANCE")
    if environment_directory:
        return load_instance(
            Path(environment_directory),
            matching_rule="KINBY_INSTANCE",
            model_override=model_override,
        )
    current_directory = Path.cwd()
    for candidate in (current_directory, *current_directory.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return load_instance(
                candidate,
                matching_rule="walk-up",
                model_override=model_override,
            )
    home_default = Path.home() / ".kinby" / "default"
    if (home_default / MANIFEST_NAME).is_file():
        return load_instance(
            home_default,
            matching_rule="home default",
            model_override=model_override,
        )
    raise InstanceNotFoundError("No kinby instance found. Run `kinby init <directory>` first.")
