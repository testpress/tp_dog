# Tasks: Service Errors & Exceptions Dashboard & Bar Drilldown

Source: `understand.md`, `technical-plan.md`

## T1 — Update Error Rate panel in Generic Service Overview to Bar Chart with Data Link
**Files:** `docker/grafana/dashboards/tracenest_generic_service_overview.json`  
**Depends on:** none  

Convert the `Error Rate Over Time` timeseries panel (ID: 7) from a smooth line graph to a bar chart (`drawStyle: "bars"`). Add a data link (`🔍 Inspect Error Traces for ${service}`) targeting `/d/generic-service-errors/generic-service-errors?var-service=${service}&var-project=${project}&var-cluster=${cluster}&${__url_time_range}`.

**Verify:** JSON syntax is valid, Grafana container reloads dashboard without errors.

---

## T2 — Create Service Errors & Exceptions Dashboard with APM Error Trace Explorer Table
**Files:** `docker/grafana/dashboards/tracenest_generic_service_errors.json`  
**Depends on:** T1  

Create the dedicated dashboard (`generic-service-errors`) containing:
1. Top navigation breadcrumb bar (`← Back to Service Overview`, `← Project Catalog`).
2. KPI Stat Cards (Total Failed Calls, Overall Error Rate %, Number of Failing Endpoints).
3. Error Volume Breakdown by Status Code Bar Chart.
4. Datadog-style Error Trace Explorer Table with columns:
   - `DATE` (timestamp with red status indicator)
   - `SERVICE` (with icon)
   - `RESOURCE` (operation/route)
   - `DURATION` (formatted duration)
   - `METHOD` (HTTP method / query type)
   - `STATUS CODE` (colored badge)
   - `SPANS` (total span count)
   - Clicking a row opens the Tempo trace waterfall/flamegraph.

**Verify:** PromQL queries and Tempo TraceQL queries return HTTP 200 via `curl`, Grafana provisions dashboard with UID `generic-service-errors`.

---

## T3 — End-to-End Verification and Test Suite Execution
**Files:** None (testing and runtime verification)  
**Depends on:** T2  

Run full automated test suite and live query verification across Prometheus, Tempo, and Grafana.

**Verify:** `pytest` passes all 183 tests; Prometheus `/api/v1/query` and Tempo `/api/search` queries succeed.
