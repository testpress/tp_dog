"""
TraceNest: Python Observability SDK for OpenTelemetry.

Provides complete distributed tracing and request waterfall instrumentation for Django and PostgreSQL.
"""

import atexit
import logging
import threading
from typing import Any, Dict, Optional, Union

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.trace import get_current_span, get_tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap

from tracenest.config import SDKConfig
from tracenest.exporter import SafeSpanExporter
from tracenest.route_context import RouteEnrichingSpanProcessor
from tracenest.integrations import BaseIntegration, get_integration_manager
from tracenest.sanitize import sanitize_query_string, sanitize_sql, sanitize_url
from tracenest.version import __version__

logger = logging.getLogger("tracenest")

_INITIALIZED = False
_INIT_LOCK = threading.Lock()
_ACTIVE_PROVIDER: Optional[TracerProvider] = None
_ACTIVE_CONFIG: Optional[SDKConfig] = None
_ATEXIT_REGISTERED = False


def init(
    project: Optional[str] = None,
    project_name: Optional[str] = None,
    cluster_name: Optional[str] = None,
    environment: Optional[str] = None,
    version: Optional[str] = None,
    endpoint: Optional[str] = None,
    traces_endpoint: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    sample_rate: Optional[float] = None,
    disabled: Optional[bool] = None,
    debug: Optional[bool] = None,
    resource_attributes: Optional[Dict[str, Any]] = None,
    integrations: Optional[Dict[str, bool]] = None,
    exporter: Optional[SpanExporter] = None,
    span_processor: Optional[SpanProcessor] = None,
    export_batch: bool = True,
    auto_patch: bool = True,
    **kwargs: Any,
) -> TracerProvider:
    """
    Initialize the TraceNest OpenTelemetry SDK.

    Configures the global TracerProvider, W3C context propagation,
    and OTLP HTTP trace exporter. Thread-safe and idempotent.

    By default, auto_patch=True so all installed integrations (Django,
    PostgreSQL, Redis, requests) are automatically detected and patched.
    A single call in your Django settings.py is all you need:

        import tracenest
        tracenest.init(project_name="my-service")

    The project name is auto-detected from Django settings if not provided.
    The OTLP endpoint defaults to OTEL_EXPORTER_OTLP_ENDPOINT env var,
    then http://localhost:4318.

    Resource attributes follow OTel semantic conventions:
      service.name, service.version, deployment.environment.name, service.instance.id
    """
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_CONFIG

    with _INIT_LOCK:
        if _INITIALIZED and _ACTIVE_PROVIDER is not None:
            logger.debug("TraceNest is already initialized. Returning existing provider.")
            return _ACTIVE_PROVIDER

        config = SDKConfig.from_env_and_kwargs(
            project=project,
            cluster_name=cluster_name,
            project_name=project_name,
            environment=environment,
            version=version,
            endpoint=endpoint,
            traces_endpoint=traces_endpoint,
            headers=headers,
            sample_rate=sample_rate,
            disabled=disabled,
            debug=debug,
            resource_attributes=resource_attributes,
            integrations=integrations,
            **kwargs,
        )
        _ACTIVE_CONFIG = config

        if config.debug:
            logger.setLevel(logging.DEBUG)

        # Build Resource attributes
        resource_data = {
            "service.name": config.project_name,
            "project_name": config.project_name,
            "cluster_name": config.cluster_name,
            "deployment.environment.name": config.environment,
            "service.version": config.version,
            "telemetry.sdk.name": "tracenest",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": __version__,
        }
        resource_data["deployment.environment"] = config.environment
        if config.tags:
            resource_data.update(config.tags)
        if config.resource_attributes:
            resource_data.update(config.resource_attributes)
        try:
            import socket, os
            resource_data.setdefault("service.instance.id", f"{socket.gethostname()}-{os.getpid()}")
        except Exception:
            pass
        resource = Resource.create(resource_data)

        # Configure Sampler
        if config.disabled:
            sampler = ALWAYS_OFF
        else:
            from tracenest.sampler import create_tracenest_sampler
            sampler = create_tracenest_sampler(
                global_sample_rate=config.sample_rate,
                ignore_endpoints=config.ignore_endpoints,
                endpoint_sample_rules=config.endpoint_sample_rules,
            )

        # Create TracerProvider
        provider = TracerProvider(resource=resource, sampler=sampler)

        # Enrich spans with the in-flight Django http.route and global tags
        static_span_attrs = {
            "project_name": config.project_name,
            "cluster_name": config.cluster_name,
        }
        if config.tags:
            static_span_attrs.update(config.tags)

        try:
            provider.add_span_processor(RouteEnrichingSpanProcessor(static_attributes=static_span_attrs))
        except Exception as exc:
            logger.debug("TraceNest: Failed to register RouteEnrichingSpanProcessor: %s", exc)

        # Configure Exporter and Processor if not disabled
        if not config.disabled:
            if exporter is None:
                if config.traces_endpoint:
                    otlp_endpoint = config.traces_endpoint
                else:
                    base_ep = config.endpoint.rstrip("/")
                    otlp_endpoint = base_ep if base_ep.endswith("/v1/traces") else f"{base_ep}/v1/traces"

                try:
                    exporter = OTLPSpanExporter(
                        endpoint=otlp_endpoint,
                        headers=config.headers or None,
                    )
                except Exception as exc:
                    logger.warning("TraceNest: Failed to create OTLPSpanExporter: %s", exc)
                    exporter = None

            if exporter is not None:
                # Wrap with SafeSpanExporter: telemetry errors NEVER crash or disrupt the host app
                safe_exporter = SafeSpanExporter(
                    exporter,
                    endpoint=otlp_endpoint if "otlp_endpoint" in locals() else None,
                )

                if span_processor is None:
                    if export_batch:
                        span_processor = BatchSpanProcessor(
                            safe_exporter,
                            max_queue_size=2048,
                            max_export_batch_size=256,
                            schedule_delay_millis=1000,
                        )
                    else:
                        span_processor = SimpleSpanProcessor(safe_exporter)

                provider.add_span_processor(span_processor)

        # Set as global tracer provider
        trace.set_tracer_provider(provider)
        _ACTIVE_PROVIDER = provider
        _INITIALIZED = True

        # Setup W3C Trace Context propagator
        set_global_textmap(TraceContextTextMapPropagator())

        # Auto-patch installed integrations if requested
        if auto_patch and not config.disabled:
            try:
                manager = get_integration_manager()
                manager.apply_integrations(config=config)
            except Exception as exc:
                logger.debug("Failed to auto-apply integrations during init: %s", exc)

        # Register shutdown on process exit exactly once
        global _ATEXIT_REGISTERED
        if not _ATEXIT_REGISTERED:
            def _shutdown():
                global _ACTIVE_PROVIDER
                try:
                    if _ACTIVE_PROVIDER is not None:
                        _ACTIVE_PROVIDER.shutdown()
                except Exception:
                    pass

            atexit.register(_shutdown)
            _ATEXIT_REGISTERED = True

        logger.info(
            "TraceNest initialized successfully (service=%s, env=%s, endpoint=%s)",
            config.project_name,
            config.environment,
            config.endpoint,
        )

        return provider


def get_config() -> SDKConfig:
    """Get active SDKConfig or default configuration."""
    if _ACTIVE_CONFIG is not None:
        return _ACTIVE_CONFIG
    return SDKConfig.from_env_and_kwargs()


def patch_all(**kwargs: Any) -> Dict[str, Any]:
    """Auto-detect and apply all installed integrations (Django, PostgreSQL, etc.)."""
    manager = get_integration_manager()
    return manager.apply_integrations(config=get_config(), **kwargs)


def _reset_for_testing() -> None:
    """Internal helper to reset singleton state between unit tests."""
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_CONFIG
    with _INIT_LOCK:
        if _ACTIVE_PROVIDER is not None:
            try:
                _ACTIVE_PROVIDER.shutdown()
            except Exception:
                pass
        _INITIALIZED = False
        _ACTIVE_PROVIDER = None
        _ACTIVE_CONFIG = None
        # Uninstrument all active integrations
        try:
            get_integration_manager().uninstrument_all()
        except Exception:
            pass
        # Reset OpenTelemetry global state safely for testing
        if hasattr(trace, "_TRACER_PROVIDER"):
            trace._TRACER_PROVIDER = None  # type: ignore
        if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
            try:
                trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore
            except Exception:
                pass


__all__ = [
    "init",
    "patch_all",
    "get_config",
    "get_tracer",
    "get_current_span",
    "get_integration_manager",
    "BaseIntegration",
    "SDKConfig",
    "sanitize_url",
    "sanitize_sql",
    "__version__",
]
