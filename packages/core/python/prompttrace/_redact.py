from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ._serialize import to_jsonable

SENSITIVE_KEY_NAMES = (
    "authorization",
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "secret",
    "cookie",
    "access_key",
    "private_key",
)


class Redactor:
    def redact(self, value: Any) -> Any:
        raise NotImplementedError


class KeyNameRedactor(Redactor):
    """Replaces values whose key name looks sensitive.

    The sensitive name must not be followed by a word character, so metric keys
    like ``tokens.total`` are not mistaken for a ``token`` secret.
    """

    _re = re.compile(
        r"(authorization|api[_-]?key|token|password|passwd|secret|cookie|"
        r"access[_-]?key|private[_-]?key)(?!\w)",
        re.IGNORECASE,
    )

    def __init__(self, names: Iterable[str] = SENSITIVE_KEY_NAMES, replacement: str = "[REDACTED]"):
        pattern = "|".join(re.escape(name) for name in names)
        self._re = re.compile(f"(?:{pattern})(?!\\w)", re.IGNORECASE)
        self.replacement = replacement

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if isinstance(key, str) and self._re.search(key):
                    out[key] = self.replacement
                else:
                    out[key] = self.redact(item)
            return out
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return value
        return value


class RegexRedactor(Redactor):
    def __init__(self, pattern: str, replacement: str = "[REDACTED]"):
        self._re = re.compile(pattern)
        self.replacement = replacement

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self._re.sub(self.replacement, value)
        return value


def apply_redactors(value: Any, redactors: Iterable[Redactor]) -> Any:
    current = to_jsonable(value)
    for redactor in redactors:
        current = redactor.redact(current)
    return current