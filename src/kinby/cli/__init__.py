"""Command-line interface."""

from __future__ import annotations

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kinby")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the kinby version and exit",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"kinby {version('kinby')}")
        return 0
    parser.print_help()
    return 0
