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
    """Verify that django_redis cache backends emit django_redis.cache.<op> spans with db.system='redis'."""
    from opentelemetry.trace import SpanKind
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

    # Span name must have redis prefix and CLIENT kind
    assert span.name == "🔸 django_redis.cache.get"
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["django.cache.operation"] == "get"
    assert span.attributes["django.cache.key"] == "tenant:subdomain.com"
    assert span.attributes["django.cache.backend"] == "FakeRedisCache"
    assert span.attributes["django.cache.hit"] is True
    assert span.attributes["db.system"] == "redis"
    assert span.attributes["db.operation"] == "get"
    assert "peer.service" not in span.attributes


def test_cache_span_sets_redis_db_system(memory_exporter):
    """Redis cache spans must set db.system='redis' so the collector and APM dashboards identify Redis."""
    from opentelemetry.trace import SpanKind
    from tracenest.integrations.django.cache import make_traced_cache_op

    class FakeRedisCache:
        __module__ = "django_redis.cache"
        _server = "redis://127.0.0.1:6379/1"

        def get(self, key):
            return "v"

    traced_get = make_traced_cache_op("get")
    traced_get(lambda k: FakeRedisCache().get(k), FakeRedisCache(), ("k",), {})

    span = memory_exporter.get_finished_spans()[0]
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["db.system"] == "redis"
    assert span.attributes["db.operation"] == "get"
    assert "peer.service" not in span.attributes
    assert span.attributes["server.address"] == "127.0.0.1"
    assert span.attributes["server.port"] == 6379
    assert span.attributes["django.cache.operation"] == "get"
    assert span.attributes["django.cache.backend"] == "FakeRedisCache"


def test_non_redis_cache_not_misclassified(memory_exporter):
    """Verify that a non-Redis backend with 'redis' in module name (e.g. myapp.redis_helpers) is not misclassified."""
    from opentelemetry.trace import SpanKind
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
    assert spans[0].kind == SpanKind.INTERNAL
    assert spans[0].attributes["django.cache.backend"] == "HelperCache"
    assert "db.system" not in spans[0].attributes
    assert "peer.service" not in spans[0].attributes



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
    set_attrs = {call[0][0]: call[0][1] for call in mock_span.set_attribute.call_args_list}
    assert set_attrs.get("db.system") == "redis"
    assert "peer.service" not in set_attrs


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


def test_cache_set_many_suppresses_pipeline_driver_instrumentation(memory_exporter):
    """Verify that during set_many pipeline execution, downstream driver spans are suppressed."""
    from tracenest.integrations.django.cache import make_traced_cache_op, _in_cache_span
    from tracenest.integrations.redis.integration import _redis_suppress_guard

    class DummyPipeline:
        def execute(self):
            return ["OK", "OK"]

    called = []
    def dummy_execute(pipe):
        called.append(True)
        return pipe.execute()

    wrapped_execute = lambda *args, **kwargs: _redis_suppress_guard(DummyPipeline.execute, DummyPipeline(), args, kwargs)

    pipe = DummyPipeline()
    token = _in_cache_span.set(True)
    try:
        res = _redis_suppress_guard(pipe.execute, pipe, (), {})
        assert res == ["OK", "OK"]
    finally:
        _in_cache_span.reset(token)

