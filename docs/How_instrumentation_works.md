# How Instrumentation Works

This document explains the internal mechanics of **OpenTelemetry tracing** and how **tp_dog auto-instrumentation** captures request waterfalls without requiring changes to application source code.

---

## 1. What is Instrumentation?

**Instrumentation** is the process of adding observability hooks to application code to measure its performance, timing, and errors.

```text
 ┌─────────────────────────────────────────────────────────────┐
 │ Manual Instrumentation      vs     Automatic Instrumentation│
 ├──────────────────────────────────┬──────────────────────────┤
 │ - Developers manually write      │ - SDK hooks into existing│
 │   span code in every view/query. │   frameworks and drivers.│
 │ - High code maintenance.         │ - Zero application code  │
 │ - Easy to miss operations.       │   changes required.      │
 └──────────────────────────────────┴──────────────────────────┘
```

tp_dog uses **auto-instrumentation**: when you call `tp_dog.init()`, it automatically discovers and patches supported libraries (Django, PostgreSQL, Redis, HTTP client) at runtime.

---

## 2. How OpenTelemetry Manages Trace Context

To understand instrumentation, you must first understand how OpenTelemetry keeps track of the "currently active span" as code executes.

```text
                         CONTEXT & ACTIVE SPAN STACK
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                                                                          │
 │  1. HTTP Request arrives ──► Context: [ django.request ]                 │
 │                                                                          │
 │  2. Middleware executes  ──► Context: [ django.request ──► Middleware ]  │
 │                                                                          │
 │  3. View executes        ──► Context: [ django.request ──► View ]        │
 │                                                                          │
 │  4. SQL Query runs       ──► Context: [ django.request ──► View ──► SQL] │
 │                                                                          │
 │  5. SQL finishes         ──► Context: [ django.request ──► View ]        │
 │                                                                          │
 │  6. View finishes        ──► Context: [ django.request ]                 │
 │                                                                          │
 └──────────────────────────────────────────────────────────────────────────┘
```

### Context Variables (`contextvars`)
Python provides thread-safe, coroutine-safe context management via `contextvars`.
- When an operation starts, OpenTelemetry stores the active span inside a context variable.
- When any downstream function creates a new child span, it automatically inspects the current context to find its parent span ID.
- This creates the hierarchical parent-child Directed Acyclic Graph (DAG) for the trace waterfall.

---

## 3. How tp_dog Auto-Instruments Libraries

tp_dog uses dynamic function wrapping (monkey-patching) via the robust [`wrapt`](https://github.com/GrahamDumpleton/wrapt) library.

```text
       APPLICATION FLOW WITH TP_DOG MONKEY-PATCHING
 ┌────────────────────────────────────────────────────────┐
 │ 1. App calls standard library function (e.g. cursor.execute)
 │                                                        │
 │ 2. Wrapper intercepts call                             │
 │    ├─► Starts OpenTelemetry child span (kind: CLIENT)  │
 │    ├─► Records start timestamp & query metadata        │
 │                                                        │
 │ 3. Wrapper calls original library function             │
 │    └─► Runs actual SQL query on PostgreSQL             │
 │                                                        │
 │ 4. Wrapper handles outcome                             │
 │    ├─► Success: Records duration, sets status = OK     │
 │    └─► Error: Records exception, sets status = ERROR   │
 │                                                        │
 │ 5. Wrapper closes span & returns result to app         │
 └────────────────────────────────────────────────────────┘
```

### The Wrapper Lifecycle (`traced_span`)

Every instrumented operation follows a structured execution lifecycle:

```python
# Conceptual wrapper lifecycle
span_name = f"🐘 {sanitize_sql(query)}"
with traced_span(span_name, kind=SpanKind.CLIENT) as span:
    span.set_attribute("db.system", "postgresql")
    span.set_attribute("db.statement", sanitize_sql(query))
    
    # Execute the real underlying database query
    result = original_execute(*args, **kwargs)
    
    return result
```

1. **Before Execution**:
   - The wrapper creates a span with appropriate attributes (sanitized SQL query, HTTP method, etc.).
   - The span is activated in the context.
2. **During Execution**:
   - The original library function runs unchanged.
3. **On Success**:
   - The span status is marked `StatusCode.OK` and closed.
4. **On Exception**:
   - If an error occurs, the wrapper catches it, records `span.record_exception(exc)`, marks `StatusCode.ERROR`, and immediately **re-raises** the exception so application error-handling continues unaffected.

---

## 4. Key Engineering Guarantees

### 1. Reentrancy Protection (`reentrant_guard`)
Some third-party libraries call their own internal methods recursively. Without protection, this could create duplicate spans or infinite loops.

tp_dog uses a lightweight reentrancy guard:
- The first (outermost) call sets an execution flag on the instance and starts a span.
- Any nested internal calls see the active flag and pass through directly without generating redundant spans.

```text
App Call ──► [Outer Wrapper: Trace Started]
                  │
                  ▼
             [Inner Method Call] ──► (Guard detected -> bypassed)
                  │
                  ▼
App Exit ◄── [Outer Wrapper: Trace Ended]
```

### 2. Context & Route Enrichment
To allow Prometheus spanmetrics to group downstream operations (like SQL queries or Redis commands) by endpoint route, child spans need access to the normalized route (`/api/products/{id}/`).

tp_dog maintains an in-flight route context (`RouteEnrichingSpanProcessor`). When a child span finishes, it automatically inherits the active `http.route` from the parent request.

### 3. Fail-Safe Isolation (`SafeSpanExporter`)
The golden rule of APM SDKs: **An observability failure must NEVER degrade or crash the host application.**

- If the OTel Collector is down, overloaded, or unreachable, tp_dog's `SafeSpanExporter` catches network exceptions and logs rate-limited warnings.
- Telemetry spans are dropped cleanly in the background without affecting user HTTP responses.

---

## 5. Summary

```text
 ┌───────────────────────────────────────────────────────────┐
 │                  How Everything Fits Together             │
 ├───────────────────────────────────────────────────────────┤
 │ 1. `tp_dog.init()` patches target modules via `wrapt`. │
 │ 2. Inbound HTTP requests start root `django.request` span.│
 │ 3. Downstream operations inherit context via `contextvars`│
 │    and generate child spans (views, SQL, Redis, HTTP).    │
 │ 4. Spans capture timing, attributes, and exceptions.      │
 │ 5. `SafeSpanExporter` batches and ships spans safely over │
 │    OTLP to the OpenTelemetry Collector.                   │
 └───────────────────────────────────────────────────────────┘
```
