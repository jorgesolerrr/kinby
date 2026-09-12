"""Delegate issue implementation to command-line coding clients."""

from kinby.factory.babysit import babysit_pull_request
from kinby.factory.pipeline import implement_ready_issue

__all__ = ["babysit_pull_request", "implement_ready_issue"]
