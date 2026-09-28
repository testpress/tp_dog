"""Unit tests for Phase 1: SDK scaffold, config, and OTel bootstrap."""

import os
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

import tracenest
from tracenest.config import SDKConfig
from tracenest.sanitize import sanitize_sql, sanitize_url


@pytest.fixture(autouse=True)
def clean_sdk_state():
    """Ensure clean SDK state before and after each test."""
    tracenest._reset_for_testing()
    yield
    tracenest._reset_for_testing()


def test_init_creates_tracer_provider():
    """Verify init() sets up TracerProvider with correct Resource attributes."""
    exporter = InMemorySpanExporter()
    provider = tracenest.init(
        project_name="test-service",
        environment="staging",
        version="1.2.3",
        resource_attributes={"custom.tag": "value123"},
        exporter=exporter,
        export_batch=False,
    )

    assert provider is not None
    assert trace.get_tracer_provider() == provider

    resource_attrs = provider.resource.attributes
    assert resource_attrs["service.name"] == "test-service"
    assert resource_attrs["deployment.environment"] == "staging"
    assert resource_attrs["service.version"] == "1.2.3"
    assert resource_attrs["telemetry.sdk.name"] == "tracenest"
    assert resource_attrs["telemetry.sdk.language"] == "python"
    assert resource_attrs["custom.tag"] == "value123"


def test_init_idempotency():
    """Verify that calling init() multiple times returns the same provider."""
    provider1 = tracenest.init(project_name="first-service")
    provider2 = tracenest.init(project_name="second-service")
    assert provider1 is provider2


def test_config_from_env_and_kwargs(monkeypatch):
    """Verify SDKConfig properly resolves env vars and kwargs overrides."""
    monkeypatch.setenv("OTEL_PROJECT_NAME", "env-service")
    monkeypatch.setenv("OTEL_ENVIRONMENT", "production")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "2.0.0")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=secret123,team=infra")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.5")

    # When kwargs are omitted, env vars are used
    cfg1 = SDKConfig.from_env_and_kwargs()
    assert cfg1.project_name == "env-service"
    assert cfg1.environment == "production"
    assert cfg1.version == "2.0.0"
    assert cfg1.endpoint == "http://otel-collector:4318"
    assert cfg1.headers == {"api-key": "secret123", "team": "infra"}
    assert cfg1.sample_rate == 0.5

    # Kwargs override env vars
    cfg2 = SDKConfig.from_env_and_kwargs(
        project_name="override-service",
        environment="dev",
        sample_rate=0.8,
        headers={"team": "product"},
    )
    assert cfg2.project_name == "override-service"
    assert cfg2.environment == "dev"
    assert cfg2.sample_rate == 0.8
    assert cfg2.headers["team"] == "product"
    assert cfg2.headers["api-key"] == "secret123"


def test_disabled_mode():
    """Verify that disabled mode prevents spans from being recorded."""
    exporter = InMemorySpanExporter()
    provider = tracenest.init(
        project_name="disabled-service",
        disabled=True,
        exporter=exporter,
        export_batch=False,
    )
    tracer = trace.get_tracer("disabled-test")
    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("key", "val")

    spans = exporter.get_finished_spans()
    assert len(spans) == 0


def test_span_creation_and_export():
    """Verify spans are created, executed within context, and exported correctly."""
    exporter = InMemorySpanExporter()
    tracenest.init(
        project_name="span-test-service",
        exporter=exporter,
        export_batch=False,
    )

    tracer = tracenest.get_tracer("test-tracer")
    with tracer.start_as_current_span("parent-operation") as parent:
        parent.set_attribute("parent.attr", "parent_val")
        current_span = tracenest.get_current_span()
        assert current_span == parent

        with tracer.start_as_current_span("child-operation") as child:
            child.set_attribute("child.attr", "child_val")
            assert tracenest.get_current_span() == child

    spans = exporter.get_finished_spans()
    assert len(spans) == 2

    child_span = spans[0]
    parent_span = spans[1]

    assert child_span.name == "child-operation"
    assert child_span.attributes["child.attr"] == "child_val"
    assert child_span.parent.span_id == parent_span.context.span_id

    assert parent_span.name == "parent-operation"
    assert parent_span.attributes["parent.attr"] == "parent_val"


def test_sanitize_url():
    """Verify URL credentials and fragments are removed properly."""
    # Basic URL
    assert sanitize_url("http://example.com/path") == "http://example.com/path"

    # URL with username and password
    url_with_auth = "https://user:secretpass@api.external.com:8080/v1/data?query=1#top"
    cleaned = sanitize_url(url_with_auth)
    assert "secretpass" not in cleaned
    assert "user" not in cleaned
    assert "#top" not in cleaned
    assert cleaned == "https://api.external.com:8080/v1/data?query=1"

    # Strip query option
    cleaned_no_query = sanitize_url(url_with_auth, strip_query=True)
    assert cleaned_no_query == "https://api.external.com:8080/v1/data"

    # None and empty
    assert sanitize_url(None) == ""
    assert sanitize_url("") == ""


def test_sanitize_sql():
    """Verify SQL strings and numbers are parameterized and whitespace is normalized."""
    # Strings and numbers replacement
    raw_query = "SELECT * FROM users WHERE id = 42 AND email = 'alice@example.com' AND active = 1"
    expected = "SELECT * FROM users WHERE id = ? AND email = ? AND active = ?"
    assert sanitize_sql(raw_query) == expected

    # Multiple whitespace and newlines
    multiline_query = """
        SELECT  id,   name
        FROM    products
        WHERE   price > 99.99
    """
    assert sanitize_sql(multiline_query) == "SELECT id, name FROM products WHERE price > ?"

    # Preserves table/column names that contain numbers or words
    col_query = "SELECT user_v2_id FROM table1 WHERE status = 'ACTIVE'"
    assert sanitize_sql(col_query) == "SELECT user_v2_id FROM table1 WHERE status = ?"

    # Truncation
    long_query = "SELECT " + ("a" * 5000)
    sanitized_long = sanitize_sql(long_query, max_length=100)
    assert len(sanitized_long) > 100
    assert sanitized_long.endswith("... [truncated]")

    # None and empty
    assert sanitize_sql(None) == ""
    assert sanitize_sql("") == ""


def test_sanitize_sql_collapses_in_list_arity():
    """IN list length must not create a distinct label per batch size."""
    short = sanitize_sql("SELECT * FROM api_product WHERE id IN (1, 2, 3)")
    long = sanitize_sql(
        "SELECT * FROM api_product WHERE id IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)"
    )
    single = sanitize_sql("SELECT * FROM api_product WHERE id IN (7)")

    assert short == "SELECT * FROM api_product WHERE id IN (?)"
    # The whole point: differing arities collapse to one identical label.
    assert short == long == single

    # Whitespace and newline variants collapse too.
    assert sanitize_sql("SELECT 1 FROM t WHERE id IN\n  (1,\n   2)") == (
        "SELECT ? FROM t WHERE id IN (?)"
    )
    assert sanitize_sql("SELECT 1 FROM t WHERE id IN(1, 2)") == (
        "SELECT ? FROM t WHERE id IN (?)"
    )


def test_sanitize_sql_preserves_in_subquery_and_composites():
    """Only flat placeholder lists may collapse; real subqueries must survive intact."""
    # A subquery is not a placeholder list and must not be collapsed to "IN (?)",
    # which would merge it with a plain value list and mis-map the query.
    subquery = "SELECT * FROM t WHERE id IN (SELECT user_id FROM orders WHERE total > 100)"
    assert sanitize_sql(subquery) == (
        "SELECT * FROM t WHERE id IN (SELECT user_id FROM orders WHERE total > ?)"
    )

    # Composite / nested element: inner parens mean the list is not provably flat.
    composite = "SELECT * FROM t WHERE (a, b) IN ((1, 2), (3, 4))"
    assert sanitize_sql(composite) == "SELECT * FROM t WHERE (a, b) IN ((?, ?), (?, ?))"

    # NOT IN collapses consistently with IN, keeping the NOT operator.
    assert sanitize_sql("SELECT * FROM t WHERE id NOT IN (1, 2, 3)") == (
        "SELECT * FROM t WHERE id NOT IN (?)"
    )

    # A function call that merely ends in "in" must not be treated as a predicate.
    assert sanitize_sql("SELECT checkin FROM t WHERE id = 1") == (
        "SELECT checkin FROM t WHERE id = ?"
    )

    # An empty IN list has no placeholders and is left alone.
    assert sanitize_sql("SELECT * FROM t WHERE id IN ()") == "SELECT * FROM t WHERE id IN ()"


def test_sanitize_sql_in_collapse_preserves_case_and_structure():
    """The predicate keyword casing and surrounding SQL must survive collapsing."""
    assert sanitize_sql("select * from t where id in (1,2)") == "select * from t where id in (?)"
    assert sanitize_sql("SELECT * FROM t WHERE a IN (1,2) AND b IN (3,4,5)") == (
        "SELECT * FROM t WHERE a IN (?) AND b IN (?)"
    )
    # A string literal containing "IN (1, 2)" is redacted before list collapsing,
    # so it must not be rewritten.
    assert sanitize_sql("SELECT * FROM t WHERE note = 'keep IN (1, 2) here'") == (
        "SELECT * FROM t WHERE note = ?"
    )


class DummyTarget:
    def greet(self, name: str) -> str:
        return f"Hello, {name}"


class MockInstalledIntegration(tracenest.BaseIntegration):
    name = "mock_installed"

    def is_installed(self) -> bool:
        return True

    def _apply_patch(self) -> None:
        def wrapper(wrapped, instance, args, kwargs):
            result = wrapped(*args, **kwargs)
            return f"Intercepted: {result}"

        self.wrap(DummyTarget, "greet", wrapper)


class MockMissingIntegration(tracenest.BaseIntegration):
    name = "mock_missing"

    def is_installed(self) -> bool:
        return False

    def _apply_patch(self) -> None:
        pass


def test_base_integration_wrap_and_unwrap():
    """Verify that BaseIntegration can wrap a method and unwrap cleanly."""
    target = DummyTarget()
    assert target.greet("Alice") == "Hello, Alice"

    integration = MockInstalledIntegration()
    assert integration.instrument() is True

    # Check that wrapper intercepted call
    assert target.greet("Alice") == "Intercepted: Hello, Alice"

    # Uninstrument and verify restored
    assert integration.uninstrument() is True
    assert target.greet("Alice") == "Hello, Alice"


def test_patch_all_discovers_installed_libs():
    """Verify patch_all discovers and instruments installed integrations."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_installed", MockInstalledIntegration)

    target = DummyTarget()
    applied = tracenest.patch_all()
    assert "mock_installed" in applied
    assert target.greet("Bob") == "Intercepted: Hello, Bob"


def test_patch_all_skips_missing_libs():
    """Verify patch_all skips uninstalled integrations."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_missing", MockMissingIntegration)

    applied = tracenest.patch_all()
    assert "mock_missing" not in applied


def test_patch_all_respects_kwargs_and_config_disables():
    """Verify that individual integrations can be disabled via patch_all kwargs or config."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_installed", MockInstalledIntegration)

    # 1. Disabled via kwargs
    applied = tracenest.patch_all(mock_installed=False)
    assert "mock_installed" not in applied

    # 2. Disabled via config
    tracenest.init(
        project_name="test-service",
        integrations={"mock_installed": False},
        export_batch=False,
    )
    applied_cfg = tracenest.patch_all()
    assert "mock_installed" not in applied_cfg


def test_safe_span_exporter_absorbs_network_exceptions():
    """Verify SafeSpanExporter absorbs ConnectionError and returns FAILURE without raising."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    from tracenest.exporter import SafeSpanExporter

    class BrokenExporter(SpanExporter):
        def export(self, spans):
            raise ConnectionRefusedError("[Errno 61] Connection refused")

        def shutdown(self):
            raise RuntimeError("Shutdown failed")

    safe = SafeSpanExporter(BrokenExporter(), endpoint="http://localhost:4318/v1/traces")
    # Export must return FAILURE and NOT raise!
    result = safe.export([])
    assert result == SpanExportResult.FAILURE

    # Shutdown must not raise!
    safe.shutdown()


def test_unreachable_collector_never_crashes_application():
    """Verify that tracing with an unreachable collector endpoint NEVER crashes or raises."""
    # Point to a dead/non-existent port on localhost
    provider = tracenest.init(
        project_name="resilience-test",
        endpoint="http://127.0.0.1:59999",
        export_batch=False,
    )
    tracer = trace.get_tracer("test.tracer")

    # Creating and ending spans when collector is dead must be 100% safe
    with tracer.start_as_current_span("resilient-span") as span:
        span.set_attribute("app.healthy", True)

    # Force flush must not raise
    provider.force_flush()
    # Shutdown must not raise
    provider.shutdown()


def test_telemetry_request_failure_returns_safe_503():
    """Verify _is_telemetry_request correctly identifies OTLP exporter endpoints."""
    from tracenest.integrations.requests.client import _is_telemetry_request

    assert _is_telemetry_request("http://localhost:4318/v1/traces", "localhost", 4318) is True
    assert _is_telemetry_request("http://tp-otel-collector:4318/v1/traces", "tp-otel-collector", 4318) is True
    assert _is_telemetry_request("https://api.github.com/users", "api.github.com", 443) is False



def test_auto_patch_true_instruments_installed_integrations():
    """Verify auto_patch=True auto-instruments installed integrations."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_installed", MockInstalledIntegration)

    target = DummyTarget()
    tracenest.init(
        project_name="auto-patch-test",
        auto_patch=True,
        exporter=InMemorySpanExporter(),
        export_batch=False,
    )
    assert target.greet("Alice") == "Intercepted: Hello, Alice"


def test_auto_patch_false_skips_instrumentation():
    """Verify auto_patch=False skips auto-instrumentation."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_installed", MockInstalledIntegration)

    target = DummyTarget()
    tracenest.init(
        project_name="no-auto-patch-test",
        auto_patch=False,
        exporter=InMemorySpanExporter(),
        export_batch=False,
    )
    # Without auto_patch, the integration is NOT instrumented
    assert target.greet("Alice") == "Hello, Alice"


def test_auto_patch_respects_integrations_config():
    """Verify auto_patch respects integrations config to disable specific integrations."""
    manager = tracenest.get_integration_manager()
    manager.register("mock_installed", MockInstalledIntegration)

    target = DummyTarget()
    tracenest.init(
        project_name="config-disable-test",
        integrations={"mock_installed": False},
        exporter=InMemorySpanExporter(),
        export_batch=False,
    )
    # Integration is disabled via config
    assert target.greet("Alice") == "Hello, Alice"


def test_middleware_init_class_exists():
    """Verify TraceNestMiddleware can be imported."""
    from tracenest.integrations.django.middleware_init import TraceNestMiddleware
    assert TraceNestMiddleware is not None


def test_sample_rate_zero():
    """Verify ALWAYS_OFF sampler drops all spans."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_OFF)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("sample-test")
    with tracer.start_as_current_span("dropped-span"):
        pass
    assert len(exporter.get_finished_spans()) == 0


def test_sample_rate_ratio():
    """Verify TraceIdRatioBased(0.5) samples approximately half of root spans."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ParentBased(root=TraceIdRatioBased(0.5)))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("sample-test")
    for i in range(100):
        with tracer.start_as_current_span(f"root-span-{i}"):
            pass
    finished = exporter.get_finished_spans()
    assert 20 <= len(finished) <= 80




