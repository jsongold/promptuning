"""PromptTrace Python SDK (V0.1 Capture).

Record function invocations as schema-compatible trace records using a
`@trace` decorator. See `PromptTrace_DESIGN.md` for the product contract.
"""

from ._core import TraceContext, trace, traced
from ._redact import KeyNameRedactor, Redactor, RegexRedactor
from ._serialize import to_jsonable
from ._sinks import (
    SDK_VERSION,
    AsyncTraceSink,
    JSONLSink,
    MemorySink,
    SyncTraceSink,
)

__all__ = [
    "TraceContext",
    "trace",
    "traced",
    "Redactor",
    "KeyNameRedactor",
    "RegexRedactor",
    "to_jsonable",
    "SyncTraceSink",
    "AsyncTraceSink",
    "MemorySink",
    "JSONLSink",
    "SDK_VERSION",
]

__version__ = SDK_VERSION