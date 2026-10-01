"""Unit tests for Redis Integration (official RedisInstrumentor)."""

import pytest
from unittest.mock import MagicMock, patch
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import tracenest
from tracenest.integrations.redis import RedisIntegration


@pytest.fixture(autouse=True)
def clean_sdk():
    tracenest._reset_for_testing()
    yield
    tracenest._reset_for_testing()


@pytest.fixture
def memory_exporter():
    exporter = InMemorySpanExporter()
    tracenest.init(
        project_name="test-redis-service",
        environment="test",
        exporter=exporter,
        export_batch=False,
    )
    return exporter


def test_redis_integration_manager_registration():
    from tracenest.integrations import get_integration_manager

    mgr = get_integration_manager()
    integ = mgr._registered_classes["redis"]
    assert integ == "tracenest.integrations.redis.RedisIntegration"
    resolved = mgr._resolve_class(integ)
    assert resolved is RedisIntegration


def test_django_redis_cache_tracing(memory_exporter):
    """Verify that django_redis cache backends emit django_redis.cache.<op> spans."""
    from tracenest.integrations.django.cache import make_traced_cache_op

    class FakeRedisCache:
        __module__ = "django_redis.cache"

        def get(self, key):
            return "cached_tenant"

    cache = FakeRedisCache()
    traced_get = make_traced_cache_op("get")

    result = traced_get(lambda k: cache.get(k), cache, ("tenant:subdomain.com",), {})
    assert result == "cached_tenant"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    # Span name must have redis prefix
    assert span.name == "🔸 django_redis.cache.get"
    assert span.attributes["django.cache.operation"] == "get"
    assert span.attributes["django.cache.key"] == "tenant:subdomain.com"
    assert span.attributes["django.cache.backend"] == "FakeRedisCache"
    assert span.attributes["django.cache.hit"] is True


def test_cache_span_does_not_claim_a_db_system(memory_exporter):
    """Cache spans must not set db.system, which would mint a phantom service.

    The collector derives ``normalized.service`` from ``db.system`` whenever a
    span has no ``peer.service`` (otel-collector-config.yaml, transform/normalize).
    Setting ``db.system="cache"`` therefore produced a service literally named
    "cache" in the ``$service`` dropdown of both generic dashboards, splitting
    Django cache work away from the Django service and double-counting it against
    the nested OTel Redis span that already reports ``db.system="redis"``.
    """
    from tracenest.integrations.django.cache import make_traced_cache_op

    class FakeRedisCache:
        __module__ = "django_redis.cache"

        def get(self, key):
            return "v"

    traced_get = make_traced_cache_op("get")
    traced_get(lambda k: FakeRedisCache().get(k), FakeRedisCache(), ("k",), {})

    span = memory_exporter.get_finished_spans()[0]
    assert "db.system" not in span.attributes
    assert "db.system.name" not in span.attributes
    assert "peer.service" not in span.attributes
    # Still fully identifiable as a cache op without the wrong attribute.
    assert span.attributes["django.cache.operation"] == "get"
    assert span.attributes["django.cache.backend"] == "FakeRedisCache"


def test_non_redis_cache_not_misclassified(memory_exporter):
    """Verify that a non-Redis backend with 'redis' in module name (e.g. myapp.redis_helpers) is not misclassified."""
    from tracenest.integrations.django.cache import make_traced_cache_op

    class HelperCache:
        __module__ = "myapp.redis_helpers"

        def get(self, key):
            return "ok"

    cache = HelperCache()
    traced_get = make_traced_cache_op("get")
    result = traced_get(lambda k: cache.get(k), cache, ("test_key",), {})
    assert result == "ok"

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "django.cache.get"
    assert spans[0].attributes["django.cache.backend"] == "HelperCache"



def test_redis_integration_apply_patch_and_uninstrument(monkeypatch):
    """Verify RedisIntegration instruments and uninstruments cleanly."""
    import sys
    import types

    dummy_redis = types.ModuleType("redis")
    dummy_redis.VERSION = (2, 9, 0)
    dummy_redis.__path__ = []
    dummy_redis.Redis = MagicMock
    dummy_redis.StrictRedis = MagicMock
    monkeypatch.setitem(sys.modules, "redis", dummy_redis)

    import importlib
    otel_redis = importlib.import_module("opentelemetry.instrumentation.redis")

    integ = RedisIntegration()
    assert integ.is_installed() is True

    with patch.object(otel_redis, "RedisInstrumentor") as mock_inst_cls:
        mock_inst = MagicMock()
        mock_inst.is_instrumented_by_opentelemetry = False
        mock_inst_cls.return_value = mock_inst

        assert integ.instrument() is True
        assert integ._instrumented is True
        mock_inst.instrument.assert_called_once()

        # Uninstrument
        mock_inst.is_instrumented_by_opentelemetry = True
        assert integ.uninstrument() is True
        assert integ._instrumented is False
        mock_inst.uninstrument.assert_called_once()


def test_redis_request_hook_prefixes_command_span():
    """Verify _tracenest_redis_request_hook updates command span with redis: prefix."""
    from tracenest.integrations.redis.integration import _tracenest_redis_request_hook

    mock_span = MagicMock()
    mock_span.name = "GET"
    mock_span.is_recording.return_value = True

    _tracenest_redis_request_hook(mock_span, None, ("GET", "key"), {})
    mock_span.update_name.assert_called_once_with("🔸 GET")


def test_cache_suppresses_downstream_driver_instrumentation():
    """Verify that during cache operation execution, downstream OTel instrumentors are suppressed."""
    from opentelemetry.instrumentation.utils import is_instrumentation_enabled
    from tracenest.integrations.django.cache import make_traced_cache_op

    instrumentation_state_during_call = []

    class FakeRedisCache:
        __module__ = "django_redis.cache"

        def get(self, key):
            instrumentation_state_during_call.append(is_instrumentation_enabled())
            return "val"

    traced_get = make_traced_cache_op("get")
    traced_get(lambda k: FakeRedisCache().get(k), FakeRedisCache(), ("k",), {})

    assert len(instrumentation_state_during_call) == 1
    assert instrumentation_state_during_call[0] is False  # Suppressed!

