"""Middleware spans — traces each class-based middleware's __call__ and process_* hooks.

Produces exact Datadog APM waterfall hierarchy:
- <module>.<MiddlewareClass>.__call__
  - <module>.<MiddlewareClass>.process_request
  - (downstream middleware chain / view)
  - <module>.<MiddlewareClass>.process_response
"""
import logging
from typing import Any, Callable, Optional

from opentelemetry.trace import SpanKind

from tracenest.config import SDKConfig
from tracenest.safety import attempt
from tracenest.tracing import traced_span
import tracenest

logger = logging.getLogger("tracenest.integrations.django.middleware")

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    """Return the config received through the integration seam, or active config."""
    if _config is not None:
        return _config
    return getattr(tracenest, "_ACTIVE_CONFIG", None)

_HOOK_METHODS = (
    "__call__",
    "process_request",
    "process_view",
    "process_response",
    "process_exception",
    "process_template_response",
)


def _make_hook_wrapper(mw_path: str, mw_short: str, method_name: str):
    span_name = f"{mw_path}.{method_name}"

    def _wrapper(wrapped_call: Callable, inst: Any, a: Any, k: Any):
        attrs = {
            "span.type": "web",
            "component": "django",
            "django.middleware": mw_path,
            "django.middleware.name": mw_short,
            "django.middleware.method": method_name,
            "resource.name": span_name,
        }
        # Pre-application telemetry: config lookup and tag merge must never
        # prevent the middleware hook from running.
        cfg = attempt(_get_config, default=None, _label="middleware_config")
        if cfg and cfg.tags:
            attempt(lambda: attrs.update(cfg.tags), _label="merge_tags")

        with traced_span(span_name, kind=SpanKind.INTERNAL, attributes=attrs, tracer_name="tracenest.django"):
            return wrapped_call(*a, **k)

    return _wrapper


def make_traced_load_middleware(integration):
    """Factory that captures the DjangoIntegration instance so we can track wrapped middlewares."""

    def traced_load_middleware(wrapped: Callable, instance: Any, args: Any, kwargs: Any):
        if getattr(instance, "_tp_middleware_instrumented", False):
            return wrapped(*args, **kwargs)

        result = wrapped(*args, **kwargs)

        try:
            from django.conf import settings
            from django.utils.module_loading import import_string

            middleware_list = getattr(settings, "MIDDLEWARE", None) or getattr(settings, "MIDDLEWARE_CLASSES", [])

            for middleware_path in middleware_list:
                if middleware_path in integration._wrapped_middleware:
                    continue
                try:
                    middleware = import_string(middleware_path)
                    if not isinstance(middleware, type):
                        logger.debug("Skipping function-based middleware %s (class-based only)", middleware_path)
                        integration._wrapped_middleware.add(middleware_path)
                        continue

                    middleware_short = middleware_path.rsplit(".", 1)[-1]

                    for method_name in _HOOK_METHODS:
                        # Check if method is defined on the class or non-object base
                        if hasattr(middleware, method_name):
                            attr = getattr(middleware, method_name, None)
                            if callable(attr):
                                try:
                                    integration.wrap(
                                        middleware,
                                        method_name,
                                        _make_hook_wrapper(middleware_path, middleware_short, method_name),
                                    )
                                except Exception as exc:
                                    logger.debug("Failed to wrap %s on %s: %s", method_name, middleware_path, exc)

                    integration._wrapped_middleware.add(middleware_path)
                except Exception as exc:
                    logger.debug("Failed to instrument middleware %s: %s", middleware_path, exc)
        except Exception as exc:
            logger.debug("Middleware instrumentation post-load failed: %s", exc)

        instance._tp_middleware_instrumented = True
        return result

    return traced_load_middleware
