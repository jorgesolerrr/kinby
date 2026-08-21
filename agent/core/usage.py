def usage_from_message(message) -> dict[str, int]:
    meta = getattr(message, "usage_metadata", None) or {}
    if meta:
        inp = int(meta.get("input_tokens") or 0)
        out = int(meta.get("output_tokens") or 0)
        total = int(meta.get("total_tokens") or (inp + out))
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": total,
            "calls": 1,
        }

    resp = getattr(message, "response_metadata", None) or {}
    usage = resp.get("usage") or {}
    inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": int(usage.get("total_tokens") or (inp + out)),
        "calls": 1,
    }
