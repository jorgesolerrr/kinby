"""Format failures from user-provided plugin code."""


def exception_message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
