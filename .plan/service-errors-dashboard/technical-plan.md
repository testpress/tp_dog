# Technical Plan: Service Errors & Exceptions Dashboard & Bar Drilldown

Source: `.plan/service-errors-dashboard/understand.md`

## 1. Technical approach
1. **Update `Error Rate Over Time` in `tracenest_generic_service_overview.json`**:
   * Change display style from smooth line to **discrete bars**: `"drawStyle": "bars"`, `"fillOpacity": 85`, `"barWidthFactor": 0.65`, `"lineInterpolation": "linear"`.
   * Add a data link to the panel:
     ```json
     "links": [
       {
         "title": "🔍 Inspect Error Traces for ${service}",
         "url": "/d/generic-service-errors/generic-service-errors?var-service=${service}&var-project=${project}&var-cluster=${cluster}&${__url_time_range}"
       }
     ]
     ```
   * Clicking a bar opens the link popup to jump directly to the Error Traces view.

2. **Create `docker/grafana/dashboards/tracenest_generic_service_errors.json` (UID: `generic-service-errors`)**:
   * **Header bar**: Breadcrumb navigation (`← Back to Service Overview`, `← Project Catalog`).
   * **Top KPI Cards**: Total Failed Calls, Service Error Rate %, and Number of Failing Endpoints.
   * **Error Volume Breakdown Bar Chart**: Timeseries with bars showing error rates/volumes by HTTP status code (`500`, `502`, `504`, etc.).
   * **Datadog-style Error Trace Explorer Table**:
     * Built using Tempo datasource table mode (`queryType: "traceql"`):
       ```traceql
       { span.normalized.service =~ `(?i)${service:regex}` && (status = error || status_code = STATUS_CODE_ERROR || error = true) }
       ```
     * Configured with columns matching the reference screenshot:
       1. `DATE`: Formatted timestamp with red error highlight.
       2. `SERVICE`: Service name with icon (e.g. `🌐 django`, `🐘 postgres`, `🔴 redis`).
       3. `RESOURCE`: The endpoint / SQL query / operation string.
       4. `DURATION`: Execution duration formatted in `s` / `ms` (`unit: "ms"` or `"s"`).
       5. `METHOD`: HTTP Method / Command (`GET`, `POST`, `PATCH`, `SQL`, etc.).
       6. `STATUS CODE`: Formatted cell badge (`type: "color-background"`, red for 5xx).
       7. `SPANS`: Total span count in the trace.
     * Clicking any row immediately launches the full Tempo trace waterfall & flamegraph!

---

## 2. Architecture / integration details

```text
┌────────────────────────────────────────────────────────────────────────┐
│ Generic Service Overview Dashboard (/d/generic-service-overview)       │
│                                                                        │
│  [Throughput Line Graph]   [Error Rate Bar Chart 📊]                   │
│                                     │ (Click Bar -> "Inspect Traces")  │
└─────────────────────────────────────┼──────────────────────────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Service Errors & Exceptions Dashboard (/d/generic-service-errors)      │
│                                                                        │
│ ┌───────────────────────┬──────────────────────┬─────────────────────┐ │
│ │ Total Failed Calls    │ Overall Error Rate % │ Failing Endpoints   │ │
│ └───────────────────────┴──────────────────────┴─────────────────────┘ │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ Error Rate Volume by Status Code (Bar Graph)                       │ │
│ └────────────────────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ Datadog-style Error Trace Explorer Table:                          │ │
│ │ DATE │ SERVICE │ RESOURCE │ DURATION │ METHOD │ STATUS CODE │ SPANS│ │
│ └────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Files to create & modify

1. **`docker/grafana/dashboards/tracenest_generic_service_errors.json`** (NEW):
   * Complete APM error explorer dashboard.
2. **`docker/grafana/dashboards/tracenest_generic_service_overview.json`** (MODIFY):
   * Convert `Error Rate Over Time` panel to bar chart and add data link.

---

## 4. Verification & Testing
1. Reload Grafana container (`docker restart observablity-grafana-1`).
2. Test Tempo TraceQL error search endpoint with `curl`.
3. Test PromQL error queries with `curl`.
4. Verify all 183 automated pytest tests pass.
