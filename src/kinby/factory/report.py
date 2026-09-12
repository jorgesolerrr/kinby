"""Serialize factory reports for routine data."""

import json
from collections.abc import Mapping


def report_json(report: Mapping[str, object]) -> str:
    """Return compact JSON for a structured factory report."""
    return json.dumps(report, separators=(",", ":"))
