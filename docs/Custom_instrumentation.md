# Custom Instrumentation Guide

This guide explains **how custom instrumentation works in tp_trace** and provides a step-by-step walkthrough for **authoring custom instrumentation for any new library, database, or background worker**.

---

## 1. How Custom Instrumentation Works in tp_trace

tp_trace uses **dynamic function wrapping (monkey-patching)** powered by [`wrapt`](https://github.com/GrahamDumpleton/wrapt), combined with OpenTelemetry's context and span APIs.

### Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                            IntegrationManager                               │
│     Discovers, instantiates, and enables/disables registered integrations   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BaseIntegration                                │
│   • is_installed()  -> Validates whether target library is available        │
│   • wrap()          -> Safe, reversible wrapt function patching             │
│   • when_imported() -> Defers patching until target module is loaded        │
│   • unwrap_all()    -> Restores original functions cleanly                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Integration Wrapper                              │
│                                                                             │
│   1. Checks reentrant_guard (prevents nested self-calls / infinite loops)   │
│   2. Opens traced_span(name, kind, attributes)                              │
│   3. Injects in-flight request route context (RouteEnrichingSpanProcessor)  │
│   4. Sanitizes sensitive arguments (SQL literals, passwords, auth tokens)   │
│   5. Executes original library function                                     │
│   6. Handles exceptions: records error on span, re-raises without crashing  │
│   7. Closes span -> batches to OTel Collector                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### Key Primitives Provided by tp_trace

tp_trace provides built-in primitives in `tp_trace.tracing` and `tp_trace.integrations.base` to eliminate boilerplate:

| Primitive | Purpose | Location |
| :--- | :--- | :--- |
| **`BaseIntegration`** | Base class managing patch lifecycle, import hooks, and unpatching. | `tp_trace.integrations.base` |
| **`traced_span()`** | Context manager handling span creation, status (`OK`/`ERROR`), exception recording, and automatic route enrichment. | `tp_trace.tracing` |
| **`reentrant_guard()`** | Guard preventing duplicate spans when wrapped libraries call their own internal methods. | `tp_trace.tracing` |
| **`sanitize_sql()` / Redaction** | Strips literal values, credentials, and PII before attributes are attached. | `tp_trace.sanitize` |

---

## 2. Step-by-Step: Adding Custom Instrumentation for Any Library

To instrument a new library (e.g. **Celery**, **PyMongo**, **Elasticsearch**, **Stripe**, or an **Internal RPC Client**), follow this 4-step recipe.

---

### Step 1: Subclass `BaseIntegration`

Create an integration class that inherits from `BaseIntegration` and implements:
1. `name`: Unique string identifier (used in `tp_trace.init(integrations={'my_lib': True})`).
2. `is_installed()`: Checks whether the target library is importable.
3. `_apply_patch()`: Applies wrappers to target classes/functions.

```python
# tp_trace/integrations/pymongo/integration.py

import importlib
import logging
from typing import Optional

from tp_trace.config import SDKConfig
from tp_trace.integrations.base import BaseIntegration
from .client import traced_mongo_command

logger = logging.getLogger("tp_trace.integrations.pymongo")


class PyMongoIntegration(BaseIntegration):
    """Deep query and command tracing for PyMongo."""

    name = "pymongo"

    def is_installed(self) -> bool:
        """Return True only if pymongo is installed in the environment."""
        try:
            importlib.import_module("pymongo")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        """Patch target PyMongo methods."""
        try:
            # Wrap pymongo.collection.Collection methods
            self.wrap(
                "pymongo.collection.Collection",
                "find",
                traced_mongo_command("find"),
            )
            self.wrap(
                "pymongo.collection.Collection",
                "insert_one",
                traced_mongo_command("insert_one"),
            )
            self.wrap(
                "pymongo.collection.Collection",
                "update_one",
                traced_mongo_command("update_one"),
            )
            self.wrap(
                "pymongo.collection.Collection",
                "delete_one",
                traced_mongo_command("delete_one"),
            )
        except Exception as exc:
            logger.debug("PyMongo patch skipped or failed: %s", exc)
```

---

### Step 2: Implement the Wrapper Function

The wrapper intercepts calls, extracts metadata, manages spans with `traced_span`, and calls the original function.

```python
# tp_trace/integrations/pymongo/client.py

from typing import Any, Callable
from opentelemetry.trace import SpanKind
from tp_trace.tracing import traced_span, reentrant_guard


def traced_mongo_command(operation_name: str) -> Callable[..., Any]:
    """Factory creating a wrapt wrapper for a PyMongo Collection method."""

    def wrapper(
        wrapped: Callable[..., Any],
        instance: Any,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        # 1. Guard against recursive internal calls
        with reentrant_guard(instance, f"_tp_trace_guard_{operation_name}") as is_outermost:
            if not is_outermost:
                return wrapped(*args, **kwargs)

            # 2. Extract collection and database metadata
            collection_name = getattr(instance, "name", "unknown")
            database_name = getattr(getattr(instance, "database", None), "name", "unknown")

            span_name = f"🍃 mongodb.{operation_name} {collection_name}"
            attributes = {
                "db.system": "mongodb",
                "db.name": database_name,
                "db.mongodb.collection": collection_name,
                "db.operation": operation_name,
            }

            # 3. Start span, run underlying command, record errors automatically
            with traced_span(span_name, kind=SpanKind.CLIENT, attributes=attributes) as span:
                result = wrapped(*args, **kwargs)
                return result

    return wrapper
```

---

### Step 3: Register the Integration with `IntegrationManager`

Register your new integration so `tp_trace.init()` will automatically detect and patch it:

```python
from tp_trace.integrations.manager import get_integration_manager
from tp_trace.integrations.pymongo.integration import PyMongoIntegration

# Register the integration class
get_integration_manager().register("pymongo", PyMongoIntegration)
get_integration_manager().register("mongodb", PyMongoIntegration)  # Alias
```

Or add it to `_BUILTIN_INTEGRATIONS` inside `tp_trace/integrations/manager.py`:

```python
_BUILTIN_INTEGRATIONS: Dict[str, str] = {
    # ... existing integrations
    "pymongo": "tp_trace.integrations.pymongo.PyMongoIntegration",
    "mongodb": "tp_trace.integrations.pymongo.PyMongoIntegration",
}
```

---

### Step 4: Verify with Unit Tests

Write a test to verify:
1. Spans are created with correct attributes and `CLIENT`/`INTERNAL` kinds.
2. Parent-child hierarchy is preserved when called inside a request or parent span.
3. Exceptions are recorded and re-raised.
4. Calling `uninstrument()` cleanly restores the original method.

```python
def test_pymongo_instrumentation(in_memory_exporter):
    from tp_trace.integrations.pymongo.integration import PyMongoIntegration
    
    integration = PyMongoIntegration()
    assert integration.instrument() is True

    # Run mock or live operation
    # ...
    
    # Clean up
    assert integration.uninstrument() is True
```

---

## 3. Manual Custom Instrumentation (In Application Code)

In addition to building automatic integrations, developers can instrument custom business logic, background jobs, or critical algorithms directly in their application code.

### A. Using Context Managers

```python
from tp_trace.tracing import traced_span
from opentelemetry.trace import SpanKind

def calculate_risk_score(user_id: int, transaction_amount: float):
    with traced_span(
        "business.risk_score_calculation",
        kind=SpanKind.INTERNAL,
        attributes={
            "business.user_id": user_id,
            "business.amount": transaction_amount,
        },
    ) as span:
        # Your custom logic here
        score = run_ml_risk_model(user_id, transaction_amount)
        
        # Add dynamic attributes or metrics
        span.set_attribute("business.risk_score", score)
        return score
```

### B. Using Function Decorators

```python
import functools
from opentelemetry.trace import SpanKind
from tp_trace.tracing import traced_span


def trace_action(action_name: str, kind: SpanKind = SpanKind.INTERNAL):
    """Decorator to trace custom functions."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with traced_span(f"action.{action_name}", kind=kind) as span:
                return fn(*args, **kwargs)
        return wrapper
    return decorator


@trace_action("generate_monthly_invoice")
def generate_invoice(account_id: str):
    ...
```

---

## 4. Context Propagation for Background Workers & Async Tasks

When passing work across threads, process boundaries, or message queues (such as **Celery**, **RQ**, or **threading**), inject and extract the **W3C `traceparent`** context header.

### Producer (Injecting Context)

```python
from opentelemetry.propagate import inject

def dispatch_background_email(recipient: str, subject: str):
    headers = {}
    # Serializes active trace context into headers: {'traceparent': '00-4bf92f...-01'}
    inject(headers)
    
    celery_task.apply_async(
        args=[recipient, subject],
        headers=headers,
    )
```

### Consumer / Worker (Extracting Context)

```python
from opentelemetry.propagate import extract
from tp_trace.tracing import traced_span
from opentelemetry.trace import SpanKind

def process_background_email(recipient: str, subject: str, request_headers: dict):
    # Extracts trace context so the worker span becomes a child of the original request
    parent_context = extract(request_headers)
    
    with traced_span(
        "celery.process_email",
        kind=SpanKind.CONSUMER,
        context=parent_context,
        attributes={"email.recipient": recipient},
    ):
        send_smtp_email(recipient, subject)
```

---

## 5. Best Practices Checklist for Custom Instrumentation

| Principle | Guideline |
| :--- | :--- |
| **Low Cardinality Span Names** | Use static operation names (e.g. `mongodb.find users`), never inject raw IDs or query literals into the span name. |
| **Sanitize Attributes** | Strip customer passwords, credit card numbers, tokens, and SQL/query literals before assigning to attributes. |
| **Correct Span Kind** | Use `SERVER` for incoming requests, `CLIENT` for downstream databases/APIs, `CONSUMER`/`PRODUCER` for queues, and `INTERNAL` for business logic. |
| **Reentrancy Protection** | Always wrap recursive-prone library methods with `reentrant_guard()`. |
| **Never Swallow Exceptions** | When catching exceptions in a wrapper, record `span.record_exception(exc)` and **always re-raise** so application error handling behaves normally. |
| **Clean Uninstrumentation** | Ensure `_remove_patch()` or `unwrap_all()` restores original methods for testing and clean teardown. |
