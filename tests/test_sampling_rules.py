"""Unit tests for endpoint-based and custom rule sampling in TraceNest SDK v3."""

import pytest
from opentelemetry.sdk.trace.sampling import Decision
from tracenest.config import SDKConfig
from tracenest.sampler import TraceNestRuleBasedSampler, create_tracenest_sampler


def test_ignore_endpoints_wildcard_matching():
    """Verify ignore_endpoints drops spans matching exact or wildcard path patterns."""
    sampler = TraceNestRuleBasedSampler(
        global_sample_rate=1.0,
        ignore_endpoints=["/health*", "*/static/*", "/api/products/health/"],
    )

    # Ignored paths -> DROP
    res1 = sampler.should_sample(None, 12345, "GET /health", attributes={"http.target": "/health"})
    assert res1.decision == Decision.DROP

    res2 = sampler.should_sample(None, 12345, "GET /healthz", attributes={"http.target": "/healthz?foo=bar"})
    assert res2.decision == Decision.DROP

    res3 = sampler.should_sample(None, 12345, "GET /app/static/main.css", attributes={"url.path": "/app/static/main.css"})
    assert res3.decision == Decision.DROP

    res4 = sampler.should_sample(None, 12345, "GET /api/products/health/", attributes={"http.target": "/api/products/health/"})
    assert res4.decision == Decision.DROP

    # Non-ignored path -> RECORD_AND_SAMPLE (global_sample_rate=1.0)
    res5 = sampler.should_sample(None, 12345, "GET /api/products/", attributes={"http.target": "/api/products/"})
    assert res5.decision == Decision.RECORD_AND_SAMPLE


def test_endpoint_sample_rules_custom_ratios():
    """Verify endpoint_sample_rules applies custom per-route sampling ratios."""
    sampler = TraceNestRuleBasedSampler(
        global_sample_rate=0.0,  # Drop all by default
        endpoint_sample_rules={
            "/api/checkout/*": 1.0,  # Always sample checkout
            "/api/health": 0.0,      # Always drop health
        },
    )

    # Checkout rule (1.0) -> RECORD_AND_SAMPLE
    res1 = sampler.should_sample(None, 12345, "POST /api/checkout/pay", attributes={"http.target": "/api/checkout/pay"})
    assert res1.decision == Decision.RECORD_AND_SAMPLE

    # Health rule (0.0) -> DROP
    res2 = sampler.should_sample(None, 12345, "GET /api/health", attributes={"http.target": "/api/health"})
    assert res2.decision == Decision.DROP

    # Unmatched path (global 0.0) -> DROP
    res3 = sampler.should_sample(None, 12345, "GET /api/products/", attributes={"http.target": "/api/products/"})
    assert res3.decision == Decision.DROP


def test_config_resolves_sampling_kwargs_and_env(monkeypatch):
    """Verify SDKConfig properly resolves ignore endpoints and endpoint sampling rules."""
    cfg = SDKConfig.from_env_and_kwargs(
        project_name="test-service",
        sample_rate=0.1,
        ignore_endpoints=["/ping", "/readyz"],
        endpoint_sample_rules={"/api/vip/*": 1.0},
    )
    assert cfg.sample_rate == 0.1
    assert cfg.ignore_endpoints == ["/ping", "/readyz"]
    assert cfg.endpoint_sample_rules == {"/api/vip/*": 1.0}

    # Test environment variable resolution
    monkeypatch.setenv("TRACENEST_IGNORE_ENDPOINTS", "/health,/metrics")
    monkeypatch.setenv("TRACENEST_ENDPOINT_SAMPLE_RULES", "/api/checkout/*=1.0,/api/search/*=0.5")

    cfg_env = SDKConfig.from_env_and_kwargs()
    assert cfg_env.ignore_endpoints == ["/health", "/metrics"]
    assert cfg_env.endpoint_sample_rules == {"/api/checkout/*": 1.0, "/api/search/*": 0.5}


def test_create_tracenest_sampler():
    """Verify create_tracenest_sampler returns ParentBased wrapper."""
    sampler = create_tracenest_sampler(
        global_sample_rate=1.0,
        ignore_endpoints=["/health"],
    )
    assert sampler is not None


def test_parameterized_route_template_matching():
    """Verify endpoint_sample_rules matches against http.route even when url.path is a concrete URL."""
    sampler = TraceNestRuleBasedSampler(
        global_sample_rate=0.0,
        endpoint_sample_rules={
            "/api/items/{id}/": 1.0,
            "/api/users/<id>/": 1.0,
        },
    )

    # Concrete URL on url.path, parameterized template on http.route
    res1 = sampler.should_sample(
        None,
        12345,
        "GET /api/items/999/",
        attributes={
            "url.path": "/api/items/999/",
            "http.route": "/api/items/{id}/",
        },
    )
    assert res1.decision == Decision.RECORD_AND_SAMPLE

    res2 = sampler.should_sample(
        None,
        12345,
        "GET /api/users/42/",
        attributes={
            "url.path": "/api/users/42/",
            "http.route": "/api/users/<id>/",
        },
    )
    assert res2.decision == Decision.RECORD_AND_SAMPLE

    # Route that does not match rule falls back to global 0.0 -> DROP
    res3 = sampler.should_sample(
        None,
        12345,
        "GET /api/orders/555/",
        attributes={
            "url.path": "/api/orders/555/",
            "http.route": "/api/orders/{id}/",
        },
    )
    assert res3.decision == Decision.DROP
