"""
Field redaction utilities. Mask sensitive values for non-superadmins so
regular admins can SEE THAT a key is set without learning its value.
"""
from typing import Any, Dict, Iterable

# Conservative blocklist — any key containing these substrings is treated
# as sensitive. Match is case-insensitive.
SENSITIVE_KEY_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "private_key", "client_secret", "auth", "key",
)


def is_sensitive_key(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    n = name.lower()
    # Whitelist obvious non-secret fields that contain "key"
    if n in {"id", "key", "primary_key", "stock_code", "sort_key"}:
        return False
    return any(h in n for h in SENSITIVE_KEY_HINTS)


def mask_value(value: Any, show_last: int = 4) -> str:
    """Return a masked version: '••••••AB12'. Empty values stay empty."""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if len(s) <= show_last:
        return "•" * len(s)
    return ("•" * max(6, len(s) - show_last)) + s[-show_last:]


def redact_dict(doc: Dict[str, Any], extra_keys: Iterable[str] = ()) -> Dict[str, Any]:
    """Recursively redact sensitive fields. Returns a NEW dict, original is
    not mutated. Designed to be cheap on small admin-config payloads."""
    if not isinstance(doc, dict):
        return doc
    extras = {e.lower() for e in extra_keys}
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if isinstance(v, dict):
            out[k] = redact_dict(v, extra_keys)
        elif isinstance(v, list):
            out[k] = [redact_dict(x, extra_keys) if isinstance(x, dict) else x for x in v]
        elif is_sensitive_key(k) or k.lower() in extras:
            out[k] = mask_value(v)
            out[f"_{k}_set"] = bool(v)  # boolean hint that the value exists
        else:
            out[k] = v
    return out
