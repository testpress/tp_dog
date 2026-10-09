"""RedisIntegration — leverages official OpenTelemetry Redis instrumentor."""

import importlib
import logging
from typing import Any, Optional

from tp_trace.config import SDKConfig
from tp_trace.integrations.base import BaseIntegration
from tp_trace.integrations.django.cache import _in_cache_span

logger = logging.getLogger("tp_trace.integrations.redis")


def _tp_trace_redis_request_hook(span: Any, instance: Any, args: Any, kwargs: Any) -> None:
    try:
        if span is not None and getattr(span, "is_recording", lambda: True)():
            name = getattr(span, "name", "")
            raw_op = name.replace("🔸", "").replace("REDIS:", "").replace("redis:", "").strip() if name else "command"
            display_name = f"🔸 {raw_op.upper()}" if raw_op else "🔸 COMMAND"
            if hasattr(span, "update_name"):
                span.update_name(display_name)
            if hasattr(span, "set_attribute"):
                span.set_attribute("db.system", "redis")
                span.set_attribute("db.name", "redis")
                span.set_attribute("normalized.service", "redis")
                span.set_attribute("normalized.operation", f"redis.{raw_op.lower()}" if raw_op else "redis.command")
                # Mask Redis connection IP/port to avoid exposing internal network topology
                span.set_attribute("net.peer.name", "?")
                span.set_attribute("net.peer.port", "?")
    except Exception as exc:
        logger.debug("redis hook/guard error", exc_info=True)


def _tp_trace_redis_response_hook(span: Any, instance: Any, response: Any) -> None:
    pass


# Backwards compatibility aliases
_tp_dog_redis_request_hook = _tp_trace_redis_request_hook
_tp_dog_redis_response_hook = _tp_trace_redis_response_hook


def _redis_suppress_guard(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    try:
        from opentelemetry.instrumentation.utils import is_instrumentation_enabled

        if not is_instrumentation_enabled() or _in_cache_span.get():
            orig = getattr(wrapped, "__wrapped__", wrapped)
            while hasattr(orig, "__wrapped__"):
                orig = orig.__wrapped__
            return orig(*args, **kwargs)
    except Exception as exc:
        logger.debug("redis hook/guard error", exc_info=True)
    return wrapped(*args, **kwargs)


class RedisIntegration(BaseIntegration):
    """Official OpenTelemetry Redis instrumentation for redis-py and asyncio clients."""

    name = "redis"

    def __init__(self, config: Optional[SDKConfig] = None) -> None:
        super().__init__(config=config)

    def is_installed(self) -> bool:
        try:
            importlib.import_module("redis")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor

            instrumentor = RedisInstrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument(
                    request_hook=_tp_trace_redis_request_hook,
                    response_hook=_tp_trace_redis_response_hook,
                )
            logger.debug("tp_trace: Official RedisInstrumentor applied successfully.")
        except Exception as exc:
            logger.debug("RedisInstrumentor patch skipped: %s", exc)

        # Wrap execute_command and pipeline execution with suppression guard to prevent duplicate child spans
        # when called from high-level cache wrappers (compatible across all OTel versions)
        targets = [
            ("redis.client.Redis", "execute_command"),
            ("redis.client.Redis", "pipeline"),
            ("redis.client.StrictRedis", "execute_command"),
            ("redis.client.StrictRedis", "pipeline"),
            ("redis.Redis", "execute_command"),
            ("redis.Redis", "pipeline"),
            ("redis.client.Pipeline", "execute"),
            ("redis.client.Pipeline", "immediate_execute_command"),
            ("redis.cluster.ClusterPipeline", "execute"),
            ("redis.asyncio.client.Pipeline", "execute"),
            ("redis.asyncio.client.Pipeline", "immediate_execute_command"),
        ]
        for target_cls, target_method in targets:
            try:
                self.wrap(target_cls, target_method, _redis_suppress_guard)
            except Exception:
                pass

    def uninstrument(self) -> bool:
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor

            instrumentor = RedisInstrumentor()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
        except Exception as exc:
            logger.debug("Failed to uninstrument redis: %s", exc)

        return super().uninstrument()


