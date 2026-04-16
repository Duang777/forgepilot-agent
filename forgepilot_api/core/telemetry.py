from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from forgepilot_api.core.logging import get_logger

logger = get_logger(__name__)

_TRACER: Any | None = None
_TELEMETRY_ENABLED = False
_TELEMETRY_INITIALIZED = False


def _safe_set_attr(span: Any, key: str, value: Any) -> None:
    try:
        if value is None:
            return
        span.set_attribute(key, value)
    except Exception:
        return


def configure_telemetry(
    *,
    enabled: bool,
    service_name: str,
    exporter: str,
    otlp_endpoint: str | None,
) -> None:
    global _TRACER, _TELEMETRY_ENABLED, _TELEMETRY_INITIALIZED
    _TELEMETRY_ENABLED = bool(enabled)
    if not enabled:
        _TRACER = None
        _TELEMETRY_INITIALIZED = True
        return
    if _TELEMETRY_INITIALIZED and _TRACER is not None:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except Exception as exc:
        logger.warning("otel enabled but dependencies missing; telemetry disabled (%s)", exc)
        _TRACER = None
        _TELEMETRY_ENABLED = False
        _TELEMETRY_INITIALIZED = True
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    processor = None
    normalized_exporter = (exporter or "console").strip().lower()
    if normalized_exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter_obj = OTLPSpanExporter(endpoint=otlp_endpoint) if otlp_endpoint else OTLPSpanExporter()
            processor = BatchSpanProcessor(exporter_obj)
        except Exception as exc:
            logger.warning("failed to init OTLP exporter; fallback to console exporter (%s)", exc)
            processor = BatchSpanProcessor(ConsoleSpanExporter())
    else:
        processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("forgepilot-api")
    _TELEMETRY_INITIALIZED = True
    logger.info("telemetry enabled exporter=%s service=%s", normalized_exporter, service_name)


def telemetry_is_enabled() -> bool:
    return _TELEMETRY_ENABLED and _TRACER is not None


@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    tracer = _TRACER
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                _safe_set_attr(span, key, value)
        yield span


def add_span_event(span: Any, name: str, attributes: dict[str, Any] | None = None) -> None:
    if span is None:
        return
    try:
        if attributes:
            span.add_event(name, attributes=attributes)
        else:
            span.add_event(name)
    except Exception:
        return
