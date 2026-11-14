"""Helpers to read text and token usage from a chat-model reply (provider-agnostic)."""


def message_text(msg) -> str:
    """Plain text of a chat-model reply (content may be a string or a list of blocks)."""
    c = msg.content
    if isinstance(c, str):
        return c
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in c)


def token_usage(msg, prompt_chars: int, answer: str) -> dict:
    """Prompt/completion tokens reported by the provider; falls back to a ~4 chars/token estimate."""
    um = getattr(msg, "usage_metadata", None) or {}
    if um.get("input_tokens") is not None:
        return {"prompt_tokens": int(um["input_tokens"]), "completion_tokens": int(um.get("output_tokens", 0)),
                "tokens_estimated": False}
    tu = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
    if tu.get("prompt_tokens") is not None:
        return {"prompt_tokens": int(tu["prompt_tokens"]), "completion_tokens": int(tu.get("completion_tokens", 0)),
                "tokens_estimated": False}
    return {"prompt_tokens": (prompt_chars + 3) // 4, "completion_tokens": (len(answer) + 3) // 4,
            "tokens_estimated": True}
