"""Cache spans — wraps django.core.cache.backends.base.BaseCache operations."""
import logging
from typing import Any, Callable, Optional

from contextvars import ContextVar
from opentelemetry.trace import SpanKind

from tracenest.config import SDKConfig
from tracenest.safety import attempt, safe_set_attribute
from tracenest.tracing import traced_span
import tracenest

logger = logging.getLogger("tracenest.integrations.django.cache")

_in_cache_span: ContextVar[bool] = ContextVar("in_cache_span", default=False)

CACHE_OPS = ["get", "set", "delete", "get_many", "set_many", "delete_many", "add", "touch", "incr", "decr", "clear"]

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    """Set the active SDKConfig for this module via the integration seam."""
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    """Return the config received through the integration seam, or global fallback."""
    return _config if _config is not None else tracenest.get_config()


def make_traced_cache_op(op_name: str):
    def _traced_op(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
        # Pre-application telemetry must never prevent the cache operation from
        # running; every step below degrades independently.
        cfg = attempt(_get_config, default=None, _label="cache_config")
        if cfg and not getattr(cfg, "cache_enabled", True):
            return wrapped(*args, **kwargs)

        # Avoid nested duplicate spans if both RedisCache and DefaultClient are wrapped
        if _in_cache_span.get():
            return wrapped(*args, **kwargs)

        backend_mod = attempt(getattr, instance.__class__, "__module__", default="", _label="cache_backend_mod")
        backend_cls = attempt(getattr, instance.__class__, "__name__", default="cache", _label="cache_backend_cls")

        if "django_redis" in backend_mod or "redis" in backend_mod.lower():
            span_name = f"🔴 django_redis.cache.{op_name}"
        else:
            span_name = f"🔴 django.cache.{op_name}"

        key = None
        if args:
            key = args[0]
        elif "key" in kwargs:
            key = kwargs["key"]
        elif "keys" in kwargs:
            key = kwargs["keys"]

        span_attrs = {
            "django.cache.operation": op_name,
            "django.cache.backend": backend_cls,
            "db.system": "cache",
        }
        if key is not None:
            if isinstance(key, (list, tuple, set)):
                span_attrs["django.cache.key"] = ", ".join(str(k) for k in key)
            else:
                span_attrs["django.cache.key"] = str(key)

        token = _in_cache_span.set(True)
        try:
            with traced_span(span_name, kind=SpanKind.INTERNAL, attributes=span_attrs, tracer_name="tracenest.django") as span:
                res = wrapped(*args, **kwargs)
                if op_name == "get":
                    safe_set_attribute(span, "django.cache.hit", res is not None)
                elif op_name == "get_many":
                    safe_set_attribute(span, "django.cache.hit", bool(res) if res is not None else False)
                return res
        finally:
            _in_cache_span.reset(token)

    return _traced_op
