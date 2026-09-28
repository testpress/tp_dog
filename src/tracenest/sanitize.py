"""Sanitization utilities for URLs and SQL statements."""

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

_WHITESPACE_RE = re.compile(r"\s+")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
# Match numeric literals not attached to word characters (e.g. '= 42', ', 123.45', 'IN (1, 2)')
_NUMERIC_LITERAL_RE = re.compile(r"(?<=[^\w\$.])\b\d+(?:\.\d+)?\b")
# Match an IN/NOT IN predicate whose value list is purely a comma-separated run of
# placeholders, e.g. "IN (?, ?, ?)" or "IN(?)". The inner group is deliberately unable to
# match a subquery ("IN (SELECT ...)") or a composite element ("IN ((?), (?))") because
# those contain tokens other than "?" and ",". This keeps the arity of the list out of the
# resulting label so one query shape yields one series, while leaving any list that is not
# provably a flat placeholder list untouched.
_IN_PLACEHOLDER_LIST_RE = re.compile(r"\b(IN)\s*\(\s*\?(?:\s*,\s*\?)*\s*\)", re.IGNORECASE)
_MAX_STATEMENT_LENGTH = 4096

_SENSITIVE_QUERY_KEYS = {
    "token", "auth", "password", "pass", "secret", "key", "apikey", "api_key",
    "access_token", "refresh_token", "id_token", "session", "sessionid", "code",
    "sig", "signature", "credential", "bearer", "private_key"
}


def sanitize_sql(sql: Optional[str], max_length: int = _MAX_STATEMENT_LENGTH) -> str:
    """
    Sanitize SQL statements by replacing literal values with placeholders.
    
    Replaces:
    - String literals: 'example' -> ?
    - Numeric literals: 42, 3.14 -> ?
    - Collapses placeholder list arity: 'IN (?, ?, ?)' -> 'IN (?)'
    - Normalizes multiple whitespace characters into a single space.
    - Truncates excessively long queries.
    """
    if not sql:
        return ""
    if not isinstance(sql, str):
        try:
            sql = str(sql)
        except Exception:
            return ""

    # Replace string literals first
    sanitized = _STRING_LITERAL_RE.sub("?", sql)
    # Replace numeric literals
    sanitized = _NUMERIC_LITERAL_RE.sub("?", sanitized)
    # Collapse the arity of placeholder-only IN lists. This runs after literal
    # substitution so that a concrete list like "IN (1, 2, 3)" has already become
    # "IN (?, ?, ?)" and is therefore eligible for collapsing.
    sanitized = _IN_PLACEHOLDER_LIST_RE.sub(r"\1 (?)", sanitized)
    # Normalize whitespace
    normalized = _WHITESPACE_RE.sub(" ", sanitized).strip()

    if len(normalized) > max_length:
        return normalized[:max_length] + " ... [truncated]"
    return normalized


def sanitize_query_string(query: Optional[str]) -> str:
    """
    Sanitize a URL query string by replacing values of sensitive parameters with REDACTED.
    """
    if not query:
        return ""
    from urllib.parse import parse_qsl, urlencode
    try:
        pairs = parse_qsl(query, keep_blank_values=True)
        sanitized_pairs = []
        for k, v in pairs:
            k_lower = k.lower()
            if (
                k_lower in _SENSITIVE_QUERY_KEYS
                or any(s in k_lower for s in ("secret", "token", "password", "apikey", "api_key", "auth"))
            ):
                sanitized_pairs.append((k, "REDACTED"))
            else:
                sanitized_pairs.append((k, v))
        return urlencode(sanitized_pairs)
    except Exception:
        return ""


def sanitize_url(url: Optional[str], strip_query: bool = False) -> str:
    """
    Sanitize a URL by stripping credentials (userinfo), fragment, and redacting sensitive query params.
    
    Optionally strips query parameters completely if strip_query is True.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # Strip userinfo (username:password) if present
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@")[-1]

        if strip_query:
            query = ""
        else:
            query = sanitize_query_string(parsed.query) if parsed.query else ""

        cleaned = urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            query,
            "",  # strip fragment
        ))
        return cleaned
    except Exception:
        return str(url)
