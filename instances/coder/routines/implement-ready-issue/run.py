"""Let only issues that just became ready-for-agent reach the model."""

import json

from kinby.plugins import tool

READY_LABEL = "ready-for-agent"


def _labels(issue: dict[str, object]) -> set[str]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        return set()
    return {
        label["name"]
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }


@tool(write=False)
def select_ready_issue(signal: dict[str, object]) -> str | None:
    """Select the issue a GitHub webhook delivery marked ready-for-agent."""
    body = signal.get("body")
    if not isinstance(body, dict):
        return None
    issue = body.get("issue")
    if not isinstance(issue, dict) or issue.get("state") != "open":
        return None
    match body.get("action"):
        case "labeled":
            label = body.get("label")
            ready = isinstance(label, dict) and label.get("name") == READY_LABEL
        case "opened" | "reopened":
            ready = READY_LABEL in _labels(issue)
        case _:
            ready = False
    if not ready:
        return None
    number = issue.get("number")
    title = issue.get("title")
    url = issue.get("html_url")
    if not isinstance(number, int) or not isinstance(title, str) or not isinstance(url, str):
        return None
    return json.dumps({"number": number, "title": title, "url": url})
