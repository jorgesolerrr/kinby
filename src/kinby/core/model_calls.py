"""Turn provider usage into recorded model-call events."""

from langchain_core.messages import UsageMetadata

from kinby.contracts import ModelCompleted


def completed_model_call(
    model: str,
    usage: UsageMetadata | None,
    *,
    started_at: float,
    completed_at: float,
) -> ModelCompleted:
    """Build a model-call event from provider usage and monotonic timestamps."""
    input_details = usage.get("input_token_details", {}) if usage is not None else {}
    return ModelCompleted(
        model=model,
        input_tokens=usage["input_tokens"] if usage is not None else 0,
        output_tokens=usage["output_tokens"] if usage is not None else 0,
        cache_read_tokens=input_details.get("cache_read", 0),
        cache_creation_tokens=input_details.get("cache_creation", 0),
        duration_ms=int((completed_at - started_at) * 1000),
    )
