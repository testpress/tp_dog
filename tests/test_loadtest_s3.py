"""Tests for loadtest_s3.py — the S3 load generator and APM verifier.

The script is stdlib-only so it can run against the sample app without extra
dependencies, which also makes its pure logic directly testable here.
"""

import io
import json
import threading
from unittest.mock import patch

import pytest

import loadtest_s3 as L


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
def test_budget_zero_means_unlimited():
    """Regression: a total of 0 must not be read as 'already exhausted'.

    The CLI uses --count 0 to mean "no limit". Treating it as zero made every
    worker exit on its first claim, so the run reported 0 samples while still
    printing a full summary block.
    """
    budget = L.Budget(0)
    assert all(budget.claim() for _ in range(1000))


def test_budget_enforces_exact_count():
    budget = L.Budget(3)
    assert sum(budget.claim() for _ in range(10)) == 3


def test_budget_is_thread_safe():
    """Concurrent workers must not over-claim a shared -n budget."""
    budget = L.Budget(200)
    granted = []
    lock = threading.Lock()

    def worker():
        local = sum(budget.claim() for _ in range(100))
        with lock:
            granted.append(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == 200


# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------
def test_percentile_nearest_rank():
    values = list(range(1, 11))  # 1..10
    assert L.percentile(values, 50) == 5
    assert L.percentile(values, 99) == 10
    assert L.percentile(values, 100) == 10


def test_percentile_of_empty_is_zero():
    assert L.percentile([], 95) == 0.0


def test_percentile_single_value():
    assert L.percentile([42.0], 50) == 42.0


# ---------------------------------------------------------------------------
# Tempo payload parsing
# ---------------------------------------------------------------------------
def _tempo_payload(*names):
    """Build a payload shaped like Tempo's /api/traces OTLP JSON response."""
    return {
        "batches": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [{"scope": {"name": "x"}, "spans": [{"name": n} for n in names]}],
            }
        ]
    }


def test_collect_span_names_reads_tempo_shape():
    """Regression: the walk must unwrap batches -> scopeSpans -> spans.

    Passing scopeSpans objects straight to a span reader silently yields no
    names, because a scopeSpans object has no 'name' key.
    """
    names = L.collect_span_names(_tempo_payload("django.request", "S3.PutObject"))
    assert names == {"django.request", "S3.PutObject"}


def test_collect_span_names_handles_multiple_batches():
    payload = {
        "batches": [
            {"scopeSpans": [{"spans": [{"name": "S3.GetObject"}]}]},
            {"scopeSpans": [{"spans": [{"name": "S3.DeleteObject"}]}]},
        ]
    }
    assert L.collect_span_names(payload) == {"S3.GetObject", "S3.DeleteObject"}


def test_collect_span_names_handles_resource_spans_shape():
    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "S3.ListObjectsV2"}]}]}]}
    assert L.collect_span_names(payload) == {"S3.ListObjectsV2"}


def test_collect_span_names_tolerates_garbage():
    assert L.collect_span_names({}) == set()
    assert L.collect_span_names(None) == set()
    assert L.collect_span_names({"batches": [{}, None, {"scopeSpans": [None]}]}) == set()


# ---------------------------------------------------------------------------
# Op <-> span name contract
# ---------------------------------------------------------------------------
def test_every_loadable_op_has_an_expected_span_name():
    """The verifier must know the span name for every op the CLI accepts.

    A missing entry would make verification report a false failure for an op
    that is in fact being traced correctly.
    """
    for op in ("put", "get", "delete", "list", "head"):
        assert op in L.SPAN_NAME_FOR_OP, f"no expected span name for op {op!r}"


def test_expected_span_names_use_the_botocore_convention():
    """botocore names spans '{rpc.service}.{rpc.method}' (extensions/types.py)."""
    for op, span in L.SPAN_NAME_FOR_OP.items():
        assert span.startswith("S3."), span
        assert span == f"S3.{span[3:]}", span


def test_sample_app_view_advertises_the_same_ops():
    """The view's VALID_OPS and the script's op set must not drift apart.

    Parsed from source rather than imported: the sample app needs Django and
    rest_framework, which are only installed in the container image.
    """
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "sample-app" / "api" / "views.py"
    text = source.read_text(encoding="utf-8")
    match = re.search(r"VALID_OPS\s*=\s*\(([^)]*)\)", text)
    assert match, "could not find S3StorageView.VALID_OPS in the sample app"
    view_ops = set(re.findall(r'"(\w+)"', match.group(1)))
    assert view_ops == set(L.SPAN_NAME_FOR_OP), (
        f"view supports {sorted(view_ops)} but the load test maps "
        f"{sorted(L.SPAN_NAME_FOR_OP)}"
    )


# ---------------------------------------------------------------------------
# do_request — the ok vs HTTP-status distinction
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body, status=200):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status

    def read(self, *_a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_do_request_treats_ok_false_as_failure_despite_http_200():
    """Regression for the core property of this script.

    S3StorageView deliberately swallows backend errors and returns HTTP 200
    with ok=false. A load test that only checked the HTTP status would report
    100% success against a dead MinIO.
    """
    body = json.dumps({"status": "traced_s3_call (NoSuchKey)", "ok": False})
    with patch.object(L.urllib.request, "urlopen", return_value=_FakeResponse(body)):
        sample = L.do_request("http://app", "get", "b", "k", timeout=5)

    assert sample.http_status == 200
    assert sample.ok is False
    assert "NoSuchKey" in sample.status


def test_do_request_treats_ok_true_as_success():
    body = json.dumps({"status": "success", "ok": True, "bytes": 31})
    with patch.object(L.urllib.request, "urlopen", return_value=_FakeResponse(body)):
        sample = L.do_request("http://app", "put", "b", "k", timeout=5)

    assert sample.ok is True
    assert sample.transport_error == ""


def test_do_request_flags_unparseable_body_as_failure():
    with patch.object(L.urllib.request, "urlopen", return_value=_FakeResponse("<html>502</html>")):
        sample = L.do_request("http://app", "put", "b", "k", timeout=5)

    assert sample.ok is False
    assert sample.status.startswith("unparseable")


def test_do_request_records_transport_error():
    with patch.object(L.urllib.request, "urlopen", side_effect=OSError("connection refused")):
        sample = L.do_request("http://app", "put", "b", "k", timeout=5)

    assert sample.transport_error != ""
    assert sample.ok is False


# ---------------------------------------------------------------------------
# summarise — failure classes must stay separate
# ---------------------------------------------------------------------------
def _s(op, **kw):
    return L.Sample(op=op, latency_ms=1.0, **kw)


def test_summarise_separates_failure_classes():
    samples = [
        _s("put", ok=True, status="success"),
        _s("put", ok=True, status="success"),
        _s("get", ok=False, status="traced_s3_call (NoSuchKey)"),
        _s("delete", ok=True, status="simulated"),
        _s("list", status="unparseable body: x"),
        _s("list", transport_error="TimeoutError: timed out"),
    ]
    summary = L.summarise(samples, elapsed=2.0)

    assert summary["total"] == 6
    assert len(summary["by_op"]["put"]) == 2
    # Each failure mode is counted in exactly one bucket.
    assert len(summary["s3_failures"]) == 1
    assert len(summary["simulated"]) == 1
    assert len(summary["unparseable"]) == 1
    assert len(summary["transport_failures"]) == 1


def test_summarise_does_not_double_count_a_simulated_response():
    """'simulated' means botocore was absent, so it is not an S3 failure."""
    samples = [_s("list", ok=True, status="simulated")]
    summary = L.summarise(samples, elapsed=1.0)
    assert summary["s3_failures"] == []
    assert len(summary["simulated"]) == 1


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------
def test_rate_limiter_paces_requests():
    import time

    limiter = L.RateLimiter(rate=50)  # 20ms apart
    start = time.monotonic()
    for _ in range(5):
        limiter.wait()
    # 5 slots at 50/s spans 80ms of scheduled time.
    assert time.monotonic() - start >= 0.06


def test_rate_limiter_uncapped_does_not_sleep():
    import time

    limiter = L.RateLimiter(rate=0)
    start = time.monotonic()
    for _ in range(100):
        limiter.wait()
    assert time.monotonic() - start < 0.5


# ---------------------------------------------------------------------------
# Tempo search retry
# ---------------------------------------------------------------------------
def test_search_with_retry_returns_as_soon_as_rows_appear():
    """Indexing lag must not be reported as a missing span."""
    calls = []

    def fake_search(url, query, limit=20):
        calls.append(query)
        # Empty twice, then rows: simulates Tempo catching up.
        return {"traces": []} if len(calls) < 3 else {"traces": [{"traceID": "abc"}]}

    with patch.object(L, "tempo_search", side_effect=fake_search), \
         patch.object(L.time, "sleep"):
        payload, ok = L.search_with_retry("http://tempo", '{ name = "S3.PutObject" }',
                                          attempts=5, delay=0)

    assert ok is True
    assert len(payload["traces"]) == 1
    assert len(calls) == 3  # stopped as soon as it found rows


def test_search_with_retry_gives_up_and_reports_not_ok():
    with patch.object(L, "tempo_search", return_value={"traces": []}), \
         patch.object(L.time, "sleep"):
        payload, ok = L.search_with_retry("http://tempo", '{ name = "X" }',
                                          attempts=3, delay=0)
    assert ok is False
    assert payload == {"traces": []}


def test_search_with_retry_propagates_unreachable_tempo():
    with patch.object(L, "tempo_search", return_value=None), \
         patch.object(L.time, "sleep"):
        payload, ok = L.search_with_retry("http://tempo", '{ name = "X" }',
                                          attempts=2, delay=0)
    assert payload is None
    assert ok is False


# ---------------------------------------------------------------------------
# Summary rendering must not explode on empty input
# ---------------------------------------------------------------------------
def test_print_summary_handles_zero_samples(capsys):
    """A run that collected nothing must still print a readable report."""
    import argparse

    args = argparse.Namespace(
        url="http://app",
        ops=["put", "get"],
        concurrency=2,
        rate=0.0,
    )
    L.print_summary(L.summarise([], elapsed=0.0), args)
    out = capsys.readouterr().out
    assert "no samples" in out
