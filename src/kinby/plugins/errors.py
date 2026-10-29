"""Format failures from user-provided plugin code."""


def exception_message(exc: Exception) -> str:
    """*exc* by its type and message, or an exception group by each exception that caused it."""
    if isinstance(exc, ExceptionGroup):
        return "; ".join(exception_message(each) for each in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"
