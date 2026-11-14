"""Read the review step's verdict from the file the reviewer wrote, never from what it said."""

from kinby.plugins.hooks import HookResult, StepEnd, hook

#: The reviewer's verdict on its first line, its findings after.
REVIEW = ".scratch/review.md"
_VERDICTS = frozenset({"clean", "changes"})


class UnreadableVerdict(RuntimeError):
    """The reviewer wrote no verdict the hook can read."""


@hook
def read_review_verdict(end: StepEnd) -> HookResult:
    """Take the review's outcome from its verdict: clean, or changes for the fix step."""
    path = end.workspace / REVIEW
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    verdict = lines[0].strip() if lines else ""
    if verdict not in _VERDICTS:
        raise UnreadableVerdict(f"{REVIEW} does not start with a verdict, clean or changes.")
    return HookResult(values={"reviewed": True}, outcome=verdict)
