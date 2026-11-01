"""Secret redaction applied to anything that leaves the sandbox boundary."""

import os
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
# GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ and fine-grained github_pat_
_GITHUB_PATTERN = re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+")


class Redactor:
    """Replaces known secret values, ``sk-ant-...`` keys and GitHub tokens with [REDACTED]."""

    def __init__(self, secrets: list[str]) -> None:
        # Longest first so a secret containing another is replaced whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Redactor:
        """Read ANTHROPIC_API_KEY once, now. The value is never logged or printed."""
        env = os.environ if environ is None else environ
        return cls([env.get("ANTHROPIC_API_KEY", "")])

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return _GITHUB_PATTERN.sub(REDACTED, _KEY_PATTERN.sub(REDACTED, text))

    def redact_obj(self, obj: Any) -> Any:
        """Recursively redact every string inside nested dicts/lists."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.redact_obj(v) for v in obj]
        return obj
