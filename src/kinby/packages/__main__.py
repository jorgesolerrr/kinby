"""The hub's candidate check: run the package check inside a prepared image, then describe it.

``python -m kinby.packages <package-id> [<instance-directory>]`` exits non-zero and
prints every failure when the package would not install. Given an instance
directory, it also validates that instance's existing package.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kinby.packages import inspect_installed_package, package_json
from kinby.packages.check import check_package


def main(argv: list[str] | None = None) -> int:
    """Write one checked descriptor and template as JSON for the hub."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) not in {1, 2}:
        print(
            "usage: python -m kinby.packages <package-id> [<instance-directory>]", file=sys.stderr
        )
        return 2
    package_id = arguments[0]
    instance = Path(arguments[1]) if len(arguments) == 2 else None
    failures = check_package(package_id, instance)
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print(package_json(inspect_installed_package(package_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
