"""Expose runtime composition without exposing individual handlers."""

from kinby.core.dispatcher import Dispatcher, build_dispatcher

__all__ = ["Dispatcher", "build_dispatcher"]
