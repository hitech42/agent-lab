"""Secret redaction applied to anything that leaves the sandbox boundary."""

import os
import re
from typing import Any

REDACTED = "[REDACTED]"
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

# Read once at import (process start); never logged or printed.
_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY") or None


def redact(text: str) -> str:
    """Replace the exact ANTHROPIC_API_KEY value and any sk-ant-... token."""
    if _API_KEY:
        text = text.replace(_API_KEY, REDACTED)
    return _KEY_PATTERN.sub(REDACTED, text)


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string inside nested dicts/lists."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
