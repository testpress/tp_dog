# Understand: Service Error Traces & Bar Drilldown

## Task / feature
1. Transform the **Error Rate Over Time** panel on the **Generic Service Overview** dashboard (`/d/generic-service-overview`) into a **Bar Chart** timeseries with a click data-link popup ("Inspect Error Traces").
2. Build the dedicated **Service Errors & Exceptions Dashboard** (`/d/generic-service-errors`) featuring a rich Datadog/APM-style **Error Trace Explorer Table** matching the user's reference design (Date with red status indicator, Service icon, Resource/Endpoint, Duration, Method, Status Code badge, Span count, and direct link to the Flamegraph waterfall).

## User requirement and intent
- Change the smooth sine-like error rate graph to a discrete **Bar Graph** showing error occurrences over time.
- Clicking any bar presents a popup link to inspect the error traces.
- Navigates to a dedicated page that displays a high-density, structured Error Traces table with:
  - Timestamp (`DATE`) with red error indicator bar
  - `SERVICE` name with icon (e.g. 🌐 `django`, 🐘 `postgres`, 🔴 `redis`)
  - `RESOURCE` / Route (`GET /api/v2.4/dashboard/`)
  - `DURATION` formatted execution time (`7.51s`, `849ms`)
  - `METHOD` (`GET`, `POST`, `PATCH`, `HEAD`, etc.)
  - `STATUS CODE` highlighted with a bold colored badge (`500`, `502`, `429`, etc.)
  - `SPANS` count in the trace (e.g. `444`, `458`)
  - Clicking any trace opens the full execution flamegraph / waterfall in Tempo.

## Current behavior
- `Error Rate Over Time` on Service Overview is a smooth line timeseries with no click interactions.
- No dedicated error trace explorer page exists in the stack.

## Target Experience
```text
Generic Service Overview
 ├── [Throughput Graph]     [Error Rate Bar Chart (Click Bar -> "Inspect Error Traces")]
                                      │
                                      ▼
Service Errors & Exceptions Dashboard (/d/generic-service-errors)
 ├── Summary KPI Cards (Total Failed Requests, Overall Error %, Failing Endpoints)
 ├── Error Volume by Status Code / Exception (Bars timeseries)
 └── Datadog-style Error Trace Explorer Table:
     ┌──────────────────┬─────────┬────────────────────────────┬──────────┬────────┬─────────────┬───────┐
     │ ▌ DATE           │ SERVICE │ RESOURCE                   │ DURATION │ METHOD │ STATUS CODE │ SPANS │
     ├──────────────────┼─────────┼────────────────────────────┼──────────┼────────┼─────────────┼───────┤
     │ ▌ 11:45:00.124   │ 🌐 django│ GET /api/v2.4/dashboard/   │ 7.51s    │ GET    │  [ 500 ]    │  458  │
     │ ▌ 11:44:56.449   │ 🌐 django│ POST /api/v2.4/attempts/   │ 8.24s    │ POST   │  [ 500 ]    │  452  │
     └──────────────────┴─────────┴────────────────────────────┴──────────┴────────┴─────────────┴───────┘
```
