"""Configuration handling for TraceNest SDK."""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


import math

_TRUE_VALUES = {"1", "true", "yes", "on", "t", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "f", "n"}


def _str_to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if not s:
        return default
    if s in _TRUE_VALUES:
        return True
    if s in _FALSE_VALUES:
        return False
    raise ValueError(
        f"TraceNest: cannot parse {val!r} as boolean; "
        f"accepted values: {sorted(_TRUE_VALUES | _FALSE_VALUES)}"
    )


def _parse_rate(val: Any, field_name: str = "sample_rate") -> float:
    if val is None:
        return 1.0
    try:
        r = float(val)
    except (TypeError, ValueError):
        raise ValueError(f"TraceNest: {field_name}={val!r} is not a valid number")
    if math.isnan(r) or math.isinf(r):
        raise ValueError(f"TraceNest: {field_name}={val!r} must be finite")
    if not (0.0 <= r <= 1.0):
        raise ValueError(f"TraceNest: {field_name}={val!r} is outside valid range [0.0, 1.0]")
    return r


def _detect_django_project_name() -> Optional[str]:
    """Try to auto-detect project name from Django settings."""
    try:
        from django.conf import settings
        # Check common Django settings for project/app name
        return (
            getattr(settings, "TRACENEST_PROJECT_NAME", None)
            or getattr(settings, "OTEL_PROJECT_NAME", None)
            or getattr(settings, "PROJECT_NAME", None)
        )
    except Exception:
        return None


def _detect_django_env() -> Optional[str]:
    """Try to auto-detect environment from Django settings."""
    try:
        from django.conf import settings
        return (
            getattr(settings, "TRACENEST_ENVIRONMENT", None)
            or getattr(settings, "ENVIRONMENT", None)
            or getattr(settings, "ENV", None)
        )
    except Exception:
        return None


def _parse_headers(headers_str: Optional[str]) -> Dict[str, str]:
    if not headers_str:
        return {}
    headers = {}
    for item in headers_str.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


DEFAULT_EXCLUDE_PATTERNS = [
    "django/forms/*",
    "debug_toolbar/*",
    "*/widgets/*",
]


@dataclass
class SDKConfig:
    """Configuration options for TraceNest SDK."""

    project_name: str = "unknown-project"
    cluster_name: Optional[str] = None
    environment: str = "development"
    version: str = "0.1.0"
    endpoint: str = "http://localhost:4318"
    traces_endpoint: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    sample_rate: float = 1.0
    disabled: bool = False
    debug: bool = False
    resource_attributes: Dict[str, Any] = field(default_factory=dict)
    integrations: Dict[str, bool] = field(default_factory=dict)
    trace_nested_templates: bool = True
    template_enabled: bool = True
    cache_enabled: bool = True
    template_exclude: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    db_two_tier_spans: bool = False
    tags: Dict[str, Any] = field(default_factory=dict)
    on_request_span: Optional[Callable] = None
    ignore_endpoints: List[str] = field(default_factory=list)
    endpoint_sample_rules: Dict[str, float] = field(default_factory=dict)
    trusted_proxies: List[str] = field(default_factory=list)
    db_role_map: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env_and_kwargs(
        cls,
        project: Optional[str] = None,
        project_name: Optional[str] = None,
        cluster_name: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
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
        **extra: Any,
    ) -> "SDKConfig":
        """Build SDKConfig by prioritizing explicit kwargs over environment variables."""

        # 1. Project Name (auto-detect from Django settings if available)
        resolved_project = (
            project_name
            or project
            or extra.get("service_name")
            or extra.get("service")
            or os.getenv("TRACENEST_PROJECT_NAME")
            or os.getenv("TRACENEST_PROJECT")
            or os.getenv("TRACENEST_SERVICE_NAME")
            or os.getenv("OTEL_SERVICE_NAME")
            or os.getenv("OTEL_PROJECT_NAME")
            or _detect_django_project_name()
            or "unknown-project"
        )

        # 2. Cluster Name
        resolved_cluster = (
            cluster_name
            or os.getenv("TRACENEST_CLUSTER_NAME")
            or os.getenv("TRACENEST_CLUSTER")
            or os.getenv("OTEL_CLUSTER_NAME")
            or "unknown-cluster"
        )

        # 2. Environment (auto-detect from Django settings if available)
        resolved_env = (
            environment
            or os.getenv("TRACENEST_ENV")
            or os.getenv("TRACENEST_ENVIRONMENT")
            or os.getenv("OTEL_ENVIRONMENT")
            or os.getenv("DEPLOYMENT_ENVIRONMENT")
            or os.getenv("ENVIRONMENT")
            or os.getenv("ENV")
            or _detect_django_env()
            or "development"
        )

        # 3. Version
        resolved_version = (
            version
            or os.getenv("TRACENEST_VERSION")
            or os.getenv("OTEL_SERVICE_VERSION")
            or os.getenv("SERVICE_VERSION")
            or "0.1.0"
        )

        # 4. Endpoints & Headers
        resolved_endpoint = (
            endpoint
            or os.getenv("TRACENEST_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or "http://localhost:4318"
        )

        resolved_traces_endpoint = (
            traces_endpoint
            or os.getenv("TRACENEST_TRACES_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or None
        )

        env_headers = _parse_headers(
            os.getenv("TRACENEST_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        )
        merged_headers = dict(env_headers)
        if headers:
            merged_headers.update(headers)

        # 5. Sample rate
        raw_rate = (
            sample_rate
            if sample_rate is not None
            else os.getenv("TRACENEST_SAMPLE_RATE")
            or os.getenv("OTEL_TRACES_SAMPLER_ARG")
            or os.getenv("SAMPLE_RATE")
        )
        resolved_sample_rate = _parse_rate(raw_rate, field_name="sample_rate") if raw_rate is not None else 1.0

        # 6. Disabled
        if disabled is not None:
            resolved_disabled = bool(disabled)
        else:
            resolved_disabled = _str_to_bool(
                os.getenv("TRACENEST_DISABLED")
                or os.getenv("OTEL_SDK_DISABLED"),
                default=False,
            )

        # 7. Debug
        if debug is not None:
            resolved_debug = bool(debug)
        else:
            resolved_debug = _str_to_bool(
                os.getenv("TRACENEST_DEBUG")
                or os.getenv("OTEL_LOG_LEVEL") == "debug",
                default=False,
            )

        # 8. Extra resource attributes & integrations
        res_attrs = dict(resource_attributes or {})
        integs = dict(integrations or {})

        # 9. Trace nested templates (default True matching Datadog APM behavior)
        if "trace_nested_templates" in extra and extra["trace_nested_templates"] is not None:
            resolved_trace_nested = _str_to_bool(extra["trace_nested_templates"], default=True)
        else:
            resolved_trace_nested = _str_to_bool(
                os.getenv("TRACENEST_DJANGO_TRACE_NESTED_TEMPLATES")
                or os.getenv("OTEL_PYTHON_DJANGO_TRACE_NESTED_TEMPLATES"),
                default=True,
            )

        # 10. Template instrumentation config (enabled + exclude patterns)
        default_exclude = list(DEFAULT_EXCLUDE_PATTERNS)
        tmpl_config = extra.get("template_instrumentation") or extra.get("TEMPLATE_INSTRUMENTATION")
        if isinstance(tmpl_config, dict):
            resolved_template_enabled = _str_to_bool(tmpl_config.get("enabled", True), default=True)
            resolved_template_exclude = tmpl_config.get("exclude", default_exclude)
        else:
            resolved_template_enabled = _str_to_bool(extra.get("template_enabled", True), default=True)
            if "template_exclude" in extra and extra["template_exclude"] is not None:
                resolved_template_exclude = extra["template_exclude"]
            elif "TRACENEST_TEMPLATE_EXCLUDE" in os.environ:
                resolved_template_exclude = [
                    p.strip() for p in os.environ["TRACENEST_TEMPLATE_EXCLUDE"].split(",") if p.strip()
                ]
            else:
                resolved_template_exclude = default_exclude

        # 10b. Cache instrumentation config
        if "cache_enabled" in extra and extra["cache_enabled"] is not None:
            resolved_cache_enabled = _str_to_bool(extra["cache_enabled"], default=True)
        elif "TRACENEST_CACHE_ENABLED" in os.environ:
            resolved_cache_enabled = _str_to_bool(os.environ["TRACENEST_CACHE_ENABLED"], default=True)
        else:
            resolved_cache_enabled = True

        # 11. 2-Tier Database Spans (Datadog Parity: connection alias -> driver db name)
        resolved_db_two_tier = _str_to_bool(
            extra.get("db_two_tier_spans", os.getenv("TRACENEST_DB_TWO_TIER_SPANS", os.getenv("TRACENEST_DB_TWO_TIER", "false"))),
            default=False,
        )

        # 12. Static tags / key-value metadata (applied to all spans and resources)
        # Explicit kwargs tags override env vars entirely (consistent with other settings)
        kwarg_tags = tags if tags is not None else extra.get("tags")
        if kwarg_tags is not None:
            resolved_tags = dict(kwarg_tags)
        else:
            resolved_tags = {}
            env_tags_str = os.getenv("TRACENEST_TAGS") or os.getenv("OTEL_RESOURCE_ATTRIBUTES")
            if env_tags_str:
                for item in env_tags_str.split(","):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        resolved_tags[k.strip()] = v.strip()

        # Merge custom arbitrary extra kwargs (e.g. server_location="us-east-1", team="core")
        KNOWN_EXTRA_KEYS = {
            "trace_nested_templates", "template_instrumentation", "TEMPLATE_INSTRUMENTATION",
            "template_enabled", "template_exclude", "db_two_tier_spans", "tags",
            "on_request_span", "ignore_endpoints", "IGNORE_ENDPOINTS",
            "endpoint_sample_rules", "endpoint_rules", "sample_rules", "ENDPOINT_SAMPLE_RULES",
            "service", "service_name", "cluster",
            "db_role_map", "DB_ROLE_MAP", "trusted_proxies", "TRUSTED_PROXIES",
            "cache_enabled", "CACHE_ENABLED",
        }
        for k, v in extra.items():
            if k not in KNOWN_EXTRA_KEYS and not k.startswith("_"):
                resolved_tags[k] = v

        # 13. Per-request span callback
        resolved_on_request_span = extra.get("on_request_span")

        # 14. Endpoint sampling rules & ignores
        kwarg_ignores = extra.get("ignore_endpoints") or extra.get("IGNORE_ENDPOINTS")
        if kwarg_ignores is not None:
            resolved_ignores = list(kwarg_ignores)
        elif "TRACENEST_IGNORE_ENDPOINTS" in os.environ:
            resolved_ignores = [p.strip() for p in os.environ["TRACENEST_IGNORE_ENDPOINTS"].split(",") if p.strip()]
        else:
            resolved_ignores = []

        kwarg_rules = (
            extra.get("endpoint_sample_rules")
            or extra.get("endpoint_rules")
            or extra.get("sample_rules")
            or extra.get("ENDPOINT_SAMPLE_RULES")
        )
        if kwarg_rules is not None:
            resolved_rules = dict(kwarg_rules)
        elif "TRACENEST_ENDPOINT_SAMPLE_RULES" in os.environ:
            resolved_rules = {}
            for item in os.environ["TRACENEST_ENDPOINT_SAMPLE_RULES"].split(","):
                item = item.strip()
                if "=" in item:
                    p, r = item.split("=", 1)
                    try:
                        resolved_rules[p.strip()] = float(r.strip())
                    except ValueError:
                        pass
        else:
            resolved_rules = {}

        kwarg_proxies = extra.get("trusted_proxies") or extra.get("TRUSTED_PROXIES")
        if kwarg_proxies is not None:
            if isinstance(kwarg_proxies, str):
                resolved_trusted_proxies = [p.strip() for p in kwarg_proxies.split(",") if p.strip()]
            else:
                resolved_trusted_proxies = list(kwarg_proxies)
        elif "TRACENEST_TRUSTED_PROXIES" in os.environ:
            resolved_trusted_proxies = [p.strip() for p in os.environ["TRACENEST_TRUSTED_PROXIES"].split(",") if p.strip()]
        else:
            resolved_trusted_proxies = []

        kwarg_db_role_map = extra.get("db_role_map") or extra.get("DB_ROLE_MAP")
        if kwarg_db_role_map is not None:
            resolved_db_role_map = dict(kwarg_db_role_map)
        elif "TRACENEST_DB_ROLE_MAP" in os.environ:
            resolved_db_role_map = {}
            for item in os.environ["TRACENEST_DB_ROLE_MAP"].split(","):
                item = item.strip()
                if "=" in item:
                    a, r = item.split("=", 1)
                    resolved_db_role_map[a.strip()] = r.strip()
        else:
            resolved_db_role_map = {}

        return cls(
            project_name=resolved_project,
            cluster_name=resolved_cluster,
            environment=resolved_env,
            version=resolved_version,
            endpoint=resolved_endpoint,
            traces_endpoint=resolved_traces_endpoint,
            headers=merged_headers,
            sample_rate=resolved_sample_rate,
            disabled=resolved_disabled,
            debug=resolved_debug,
            resource_attributes=res_attrs,
            integrations=integs,
            trace_nested_templates=resolved_trace_nested,
            template_enabled=resolved_template_enabled,
            cache_enabled=resolved_cache_enabled,
            template_exclude=resolved_template_exclude,
            db_two_tier_spans=resolved_db_two_tier,
            tags=resolved_tags,
            on_request_span=resolved_on_request_span,
            ignore_endpoints=resolved_ignores,
            endpoint_sample_rules=resolved_rules,
            trusted_proxies=resolved_trusted_proxies,
            db_role_map=resolved_db_role_map,
        )
