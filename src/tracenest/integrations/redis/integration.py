"""RedisIntegration — leverages official OpenTelemetry Redis instrumentor."""

import importlib
import logging
from typing import Any, Optional

from tracenest.config import SDKConfig
from tracenest.integrations.base import BaseIntegration

logger = logging.getLogger("tracenest.integrations.redis")


def _tracenest_redis_request_hook(span: Any, instance: Any, args: Any, kwargs: Any) -> None:
    try:
        if span is not None and getattr(span, "is_recording", lambda: True)():
            name = getattr(span, "name", "")
            if name and not name.startswith("🔸") and not name.startswith("REDIS:") and not name.startswith("redis:"):
                if hasattr(span, "update_name"):
                    span.update_name(f"🔸 {name}")
            if hasattr(span, "set_attribute"):
                span.set_attribute("peer.service", "redis")
                span.set_attribute("db.system", "redis")
                span.set_attribute("db.name", "redis")
    except Exception:
        pass


def _redis_suppress_guard(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    try:
        from opentelemetry.instrumentation.utils import is_instrumentation_enabled

        if not is_instrumentation_enabled():
            orig = getattr(wrapped, "__wrapped__", wrapped)
            while hasattr(orig, "__wrapped__"):
                orig = orig.__wrapped__
            return orig(*args, **kwargs)
    except Exception:
        pass
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
                instrumentor.instrument(request_hook=_tracenest_redis_request_hook)
            logger.debug("TraceNest: Official RedisInstrumentor applied successfully.")
        except Exception as exc:
            logger.debug("RedisInstrumentor patch skipped: %s", exc)

        # Wrap execute_command and pipeline with suppression guard to prevent duplicate child spans
        # when called from high-level cache wrappers (compatible across all OTel versions)
        targets = [
            ("redis.client.Redis", "execute_command"),
            ("redis.client.Redis", "pipeline"),
            ("redis.client.StrictRedis", "execute_command"),
            ("redis.client.StrictRedis", "pipeline"),
            ("redis.Redis", "execute_command"),
            ("redis.Redis", "pipeline"),
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


