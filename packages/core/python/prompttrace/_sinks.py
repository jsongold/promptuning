from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, MutableSequence, Protocol, Sequence, Tuple

SearchValue = str | int | float | bool
JsonValue = Any
KeyResolver = Any
ArtifactResolver = Any
MetricResolver = Any

SDK_VERSION = "0.1.0"


class SyncTraceSink(Protocol):
    def append(self, record: Mapping[str, Any]) -> None: ...


class AsyncTraceSink(Protocol):
    async def append(self, record: Mapping[str, Any]) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.records: MutableSequence[Mapping[str, Any]] = []

    def append(self, record: Mapping[str, Any]) -> None:
        self.records.append(record)


class JSONLSink:
    def __init__(self, path: str, *, append: bool = True):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        self._file = open(path, mode, encoding="utf-8")

    def append(self, record: Mapping[str, Any]) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        self._file.write("\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "JSONLSink":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _emit(sink: Any, record: Dict[str, Any]) -> None:
    append = getattr(sink, "append", None)
    if append is None:
        raise TypeError("sink must define append(record)")
    result = append(record)
    if hasattr(result, "__await__"):
        raise TypeError(
            "sink.append returned a coroutine but no running event loop is available; "
            "use an async-capable context for async sinks"
        )