"""What a vanilla instance declares on this image, read by the hub when it prepares one.

``python -m kinby.setup`` prints it as JSON.
"""

from __future__ import annotations

from kinby.setup import vanilla_description


def main() -> int:
    print(vanilla_description().model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
