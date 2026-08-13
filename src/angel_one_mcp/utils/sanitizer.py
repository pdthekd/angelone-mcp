# src/angel_one_mcp/utils/sanitizer.py
from typing import Any, Dict, List

SENSITIVE_KEYS = {
    "clientcode",
    "jwttoken",
    "refreshtoken",
    "feedtoken",
    "password",
    "token",
    "totp",
    "api_key",
    "apikey",
    "pin",
    "pwd",
    "secret",
}


def _is_sensitive_key(k: str) -> bool:
    return k is not None and k.lower() in SENSITIVE_KEYS


def redact_sensitive_keys(data: Any) -> Any:
    """
    Recursively redact sensitive keys in dicts and lists.
    Returns a new data structure (does not mutate in place).
    """
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k is None:
                out[k] = redact_sensitive_keys(v)
            elif _is_sensitive_key(k):
                out[k] = "<REDACTED>"
            else:
                out[k] = redact_sensitive_keys(v)
        return out
    elif isinstance(data, list):
        return [redact_sensitive_keys(item) for item in data]
    else:
        # primitive -> return as-is
        return data
