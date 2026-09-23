from __future__ import annotations

import functools
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ._redact import Redactor, apply_redactors
from ._serialize import dumps, to_jsonable
from ._sinks import SDK_VERSION, _emit

logger = logging.getLogger("prompttrace")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class TraceContext:
    operation: str
    args: tuple
    kwargs: dict
    result: Any = None
    error: BaseException | None = None
    started_at: str = ""
    ended_at: str = ""
    duration_ms: float = 0.0


@dataclass
class _Options:
    sink: Any
    operation: str | None = None
    keys: Any = None
    artifacts: Callable[[TraceContext], dict] | None = None
    metrics: Callable[[TraceContext], dict] | None = None
    capture_input: bool = True
    capture_output: bool = True
    capture_stack: bool = True
    redactors: Sequence[Redactor] = field(default_factory=tuple)
    max_payload_bytes: int = 1_000_000
    strict: bool = False


def _resolve_keys(keys: Any, args: tuple, kwargs: dict) -> dict:
    if keys is None:
        return {}
    if callable(keys):
        resolved = keys(args, kwargs)
    else:
        resolved = keys
    if not isinstance(resolved, dict):
        raise TypeError("keys must resolve to a dict")
    return dict(resolved)


def _capture_error(error: BaseException, with_stack: bool) -> dict:
    record: dict[str, Any] = {"message": str(error) or error.__class__.__name__}
    record["type"] = error.__class__.__name__
    if with_stack:
        import traceback

        record["stack"] = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    return record


def _fit_payload(record: dict, max_bytes: int) -> dict:
    if len(dumps(record)) <= max_bytes:
        return record
    artifacts = record["artifacts"]
    fitted: dict[str, Any] = {}
    for key, value in artifacts.items():
        if isinstance(value, str) and len(value) > 256:
            fitted[key] = value[:256] + "..."
        else:
            fitted[key] = value
    fitted["_prompttrace"] = {"truncated": True}
    record["artifacts"] = fitted
    if len(dumps(record)) <= max_bytes:
        return record
    record["artifacts"] = {"_prompttrace": {"truncated": True}}
    return record


def _make_record(
    options: _Options,
    fn: Callable,
    args: tuple,
    kwargs: dict,
    started_at: str,
    ended_at: str,
    duration_ms: float,
    context: TraceContext,
) -> dict:
    keys = _resolve_keys(options.keys, args, kwargs)
    artifacts: dict[str, Any] = {}
    if options.capture_input:
        artifacts["input"] = to_jsonable({"args": args, "kwargs": kwargs})
    if options.capture_output and context.error is None:
        artifacts["output"] = context.result
    if options.artifacts is not None:
        extra = options.artifacts(context)
        if extra is not None:
            if not isinstance(extra, dict):
                raise TypeError("artifacts resolver must return a dict")
            artifacts.update(extra)
    metrics: dict[str, float] = {}
    if options.metrics is not None:
        resolved = options.metrics(context)
        if resolved is not None:
            if not isinstance(resolved, dict):
                raise TypeError("metrics resolver must return a dict")
            metrics = dict(resolved)

    record = {
        "schemaVersion": "1.0",
        "id": str(uuid.uuid4()),
        "operation": context.operation,
        "startedAt": started_at,
        "endedAt": ended_at,
        "durationMs": round(duration_ms, 3),
        "status": "error" if context.error is not None else "ok",
        "keys": keys,
        "artifacts": artifacts,
        "metrics": metrics,
        "sdk": {"language": "python", "version": SDK_VERSION},
    }
    if context.error is not None:
        record["error"] = _capture_error(context.error, options.capture_stack)
    return record


def _emit_record(options: _Options, record: dict) -> None:
    safe = apply_redactors(record, options.redactors)
    safe["metrics"] = record["metrics"]
    fitted = _fit_payload(safe, options.max_payload_bytes)
    _emit(options.sink, fitted)


def _report_failure(exc: BaseException) -> None:
    logger.warning("prompttrace failed to record trace: %s", exc)


def trace(
    *,
    sink: Any,
    operation: str | None = None,
    keys: Any = None,
    artifacts: Callable[[TraceContext], dict] | None = None,
    metrics: Callable[[TraceContext], dict] | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
    capture_stack: bool = True,
    redactors: Sequence[Redactor] = (),
    max_payload_bytes: int = 1_000_000,
    strict: bool = False,
) -> Callable:
    options = _Options(
        sink=sink,
        operation=operation,
        keys=keys,
        artifacts=artifacts,
        metrics=metrics,
        capture_input=capture_input,
        capture_output=capture_output,
        capture_stack=capture_stack,
        redactors=tuple(redactors),
        max_payload_bytes=max_payload_bytes,
        strict=strict,
    )

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            return _wrap_async(options, fn)
        return _wrap_sync(options, fn)

    return decorator


def traced(*, sink: Any, **kwargs: Any) -> Callable:
    return trace(sink=sink, **kwargs)


def _wrap_sync(options: _Options, fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = _utc_now()
        started = time.monotonic()
        context = TraceContext(
            operation=options.operation or fn.__qualname__,
            args=args,
            kwargs=kwargs,
            started_at=started_at,
        )
        result = None
        error: BaseException | None = None
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            error = exc
        context.result = result
        context.error = error
        context.ended_at = _utc_now()
        context.duration_ms = (time.monotonic() - started) * 1000
        try:
            record = _make_record(
                options,
                fn,
                args,
                kwargs,
                started_at,
                context.ended_at,
                context.duration_ms,
                context,
            )
            _emit_record(options, record)
        except BaseException as record_error:  # noqa: BLE001
            if options.strict:
                if error is not None:
                    raise error from record_error
                raise record_error
            _report_failure(record_error)
        if error is not None:
            raise error
        return result

    return wrapper


def _wrap_async(options: _Options, fn: Callable) -> Callable:
    sink_append = getattr(options.sink, "append", None)
    is_async_sink = inspect.iscoroutinefunction(sink_append)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = _utc_now()
        started = time.monotonic()
        context = TraceContext(
            operation=options.operation or fn.__qualname__,
            args=args,
            kwargs=kwargs,
            started_at=started_at,
        )
        result = None
        error: BaseException | None = None
        try:
            result = await fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            error = exc
        context.result = result
        context.error = error
        context.ended_at = _utc_now()
        context.duration_ms = (time.monotonic() - started) * 1000
        try:
            record = _make_record(
                options,
                fn,
                args,
                kwargs,
                started_at,
                context.ended_at,
                context.duration_ms,
                context,
            )
            safe = apply_redactors(record, options.redactors)
            safe["metrics"] = record["metrics"]
            fitted = _fit_payload(safe, options.max_payload_bytes)
            if is_async_sink:
                await options.sink.append(fitted)
            else:
                options.sink.append(fitted)
        except BaseException as record_error:  # noqa: BLE001
            if options.strict:
                if error is not None:
                    raise error from record_error
                raise record_error
            _report_failure(record_error)
        if error is not None:
            raise error
        return result

    return wrapper