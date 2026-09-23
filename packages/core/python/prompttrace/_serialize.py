from __future__ import annotations

import datetime
import json
from typing import Any


class SerializationError(Exception):
    pass


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, (datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return repr(obj)
    if isinstance(obj, set):
        return list(obj)
    return str(obj)


def to_jsonable(value: Any) -> Any:
    return _walk(value, set())


def _walk(value: Any, seen: set[int]) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        if id(value) in seen:
            return "<circular>"
        seen.add(id(value))
        out = {}
        for key, item in value.items():
            try:
                out[str(key)] = _walk(item, seen)
            except Exception:
                out[str(key)] = _safe_string(item)
        seen.discard(id(value))
        return out
    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            return "<circular>"
        seen.add(id(value))
        out = []
        for item in value:
            try:
                out.append(_walk(item, seen))
            except Exception:
                out.append(_safe_string(item))
        seen.discard(id(value))
        return out
    try:
        return _walk(_default(value), seen)
    except Exception:
        return _safe_string(value)


def _safe_string(value: Any) -> str:
    try:
        text = str(value)
    except Exception:
        return "<unprintable>"
    return text[:1000]


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":"))