#!/usr/bin/env bash
# ==============================================================================
# generate_anomaly.sh - tp_trace APM Anomaly & Incident Generator
# ==============================================================================
# Injects targeted or mixed anomalies to demonstrate the Exception-Driven
# "Needs Attention" / Operational System Overview dashboard.
#
# Available Scenarios:
#   1. mixed / 10+  - Generates 10+ distinct simultaneous issues (Critical, Warning, Info)
#   2. postgres     - PostgreSQL latency bottleneck (>30% downstream time, elevated P95)
#   3. redis        - Redis latency bottleneck (>20% downstream time, elevated P95)
#   4. internal     - Django internal compute latency (P95 > 800ms, downstream < 40%)
#   5. error        - Multi-endpoint 5xx error rate spikes
#   6. db-error     - PostgreSQL & Multi-DB connection / SQL execution failures
#   7. traffic      - Traffic surge anomaly (>200% RPS baseline deviation, healthy latency)
#   8. chaos        - Aggressive chaos flood across all subsystems
#   9. healthy      - Clean baseline traffic (restores 0 active issues)
# ==============================================================================

set -e

BASE_URL="${BASE_URL:-http://localhost:8001}"
SCENARIO=""
DURATION=60
CONCURRENCY=4
SLEEP_DELAY=0.02
ERROR_RATE=100
DELAY=2.2
VERBOSE=0

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_BLUE="\033[34m"
C_CYAN="\033[36m"
C_MAGENTA="\033[35m"

# Print usage
usage() {
  echo -e "${C_BOLD}Usage: $0 [scenario] [OPTIONS]${C_RESET}"
  echo ""
  echo -e "${C_BOLD}Scenarios:${C_RESET}"
  echo -e "  ${C_MAGENTA}mixed${C_RESET} | ${C_MAGENTA}10+${C_RESET} | ${C_MAGENTA}--mixed${C_RESET}       ${C_BOLD}(Default)${C_RESET} Generate 10+ simultaneous mixed issues (Errors, Latencies, Surges)"
  echo -e "  ${C_RED}postgres${C_RESET} | ${C_RED}--postgres${C_RESET}     Inject PostgreSQL latency bottleneck (>30% time, P95 > 250ms)"
  echo -e "  ${C_RED}redis${C_RESET}    | ${C_RED}--redis${C_RESET}        Inject Redis operations latency bottleneck (>20% time, P95 > 150ms)"
  echo -e "  ${C_YELLOW}internal${C_RESET} | ${C_YELLOW}--internal${C_RESET}     Inject Django internal processing latency (P95 > 800ms)"
  echo -e "  ${C_RED}error${C_RESET}    | ${C_RED}--error${C_RESET}        Inject 500 error rate spikes across multiple endpoints"
  echo -e "  ${C_RED}db-error${C_RESET} | ${C_RED}--db-error${C_RESET}     Inject PostgreSQL syntax errors and multi-DB failures"
  echo -e "  ${C_BLUE}traffic${C_RESET}  | ${C_BLUE}--traffic${C_RESET}      Inject traffic surge anomaly (>200% baseline RPS)"
  echo -e "  ${C_MAGENTA}chaos${C_RESET}    | ${C_MAGENTA}--chaos${C_RESET}        Aggressive multi-issue chaos across all systems"
  echo -e "  ${C_GREEN}healthy${C_RESET}  | ${C_GREEN}--healthy${C_RESET}      Send clean baseline traffic (restores 0 active issues)"
  echo ""
  echo -e "${C_BOLD}Options:${C_RESET}"
  echo "  -e, --error-rate PCT Target error rate percentage (1-100, default: 100)"
  echo "  --delay SEC          Artificial delay in seconds (default: 2.2s)"
  echo "  -d, --duration N     Duration in seconds to run injection (default: 60)"
  echo "  -c, --concurrency N  Number of concurrent workers (default: 4)"
  echo "  -s, --sleep SEC      Sleep between requests in seconds (default: 0.02)"
  echo "  -u, --url URL        Base URL (default: http://localhost:8001)"
  echo "  -v, --verbose        Print response details"
  echo "  -h, --help           Show this help message"
  echo ""
  echo -e "${C_BOLD}Examples:${C_RESET}"
  echo "  $0 10+                          # Generate 10+ mixed issues (Errors + DB + Cache + Traffic)"
  echo "  $0 mixed -d 120 -c 6            # 10+ issues for 2 minutes with 6 workers"
  echo "  $0 postgres --delay 3.0         # Inject 3.0s PostgreSQL queries"
  echo "  $0 redis --delay 2.5            # Inject 2.5s Redis operations"
  echo "  $0 error -e 50 -c 8             # Generate 50% error rate across endpoints"
  echo "  $0 healthy                      # Restore healthy baseline"
  exit 0
}

# Parse positional and flagged arguments
while [[ "$#" -gt 0 ]]; do
  case $1 in
  mixed | 10+ | 10 | --mixed | --10+ | --all-issues) SCENARIO="mixed" ;;
  postgres | --postgres | --slow-postgres) SCENARIO="postgres" ;;
  redis | --redis | --slow-redis) SCENARIO="redis" ;;
  internal | django | --internal | --django | --slow-django) SCENARIO="internal" ;;
  error | errors | --error | --errors | --error-spike) SCENARIO="error" ;;
  db-error | --db-error | db_error) SCENARIO="db-error" ;;
  traffic | surge | --traffic | --surge) SCENARIO="traffic" ;;
  chaos | all | --chaos | --all) SCENARIO="chaos" ;;
  healthy | normal | clean | --healthy | --normal | --clean) SCENARIO="healthy" ;;
  -e | --error-rate | --percent)
    ERROR_RATE="$2"
    shift
    ;;
  --delay)
    DELAY="$2"
    shift
    ;;
  -d | --duration)
    DURATION="$2"
    shift
    ;;
  -c | --concurrency)
    CONCURRENCY="$2"
    shift
    ;;
  -s | --sleep)
    SLEEP_DELAY="$2"
    shift
    ;;
  -u | --url)
    BASE_URL="$2"
    shift
    ;;
  -v | --verbose) VERBOSE=1 ;;
  -h | --help) usage ;;
  *)
    echo -e "${C_RED}Unknown option or scenario: $1${C_RESET}"
    echo "Run '$0 --help' for usage."
    exit 1
    ;;
  esac
  shift
done

if [ -z "$SCENARIO" ]; then
  echo -e "${C_YELLOW}No scenario specified. Defaulting to: mixed (10+ issues)${C_RESET}"
  SCENARIO="mixed"
fi

# Banner
echo -e "${C_CYAN}${C_BOLD}"
echo "================================================================="
echo "   ⚡ tp_trace APM Anomaly & Incident Generator"
echo "   Target URL:     ${BASE_URL}"
echo "   Scenario:       ${SCENARIO}"
if [ "$SCENARIO" = "mixed" ]; then
  echo "   Active Issues:  10+ distinct simultaneous issues"
fi
if [ "$SCENARIO" = "error" ] || [ "$ERROR_RATE" -ne 100 ]; then
  echo "   Target Error %: ${ERROR_RATE}%"
fi
if [ "$SCENARIO" = "postgres" ] || [ "$SCENARIO" = "redis" ] || [ "$SCENARIO" = "internal" ] || [ "$SCENARIO" = "mixed" ]; then
  echo "   Latency Delay:  ${DELAY}s"
fi
echo "   Duration:       ${DURATION}s"
echo "   Concurrency:    ${CONCURRENCY} worker(s)"
echo "   Sleep Delay:    ${SLEEP_DELAY}s"
echo "=================================================================${C_RESET}"

# Send single HTTP request helper
send_req() {
  local method="$1"
  local path="$2"
  local data="$3"
  local label="$4"

  local url="${BASE_URL}${path}"
  local http_code
  local start_ts end_ts duration_ms

  start_ts=$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')

  if [ "$method" = "POST" ]; then
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "$data" "$url" --max-time 15 2>/dev/null || echo "000")
  else
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$url" --max-time 15 2>/dev/null || echo "000")
  fi

  end_ts=$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')
  duration_ms=$(( (end_ts - start_ts) / 1000000 ))

  local color="$C_GREEN"
  if [[ "$http_code" =~ ^5 ]]; then
    color="$C_RED"
  elif [[ "$http_code" =~ ^4 ]]; then
    color="$C_YELLOW"
  elif [ "$duration_ms" -gt 1000 ]; then
    color="$C_MAGENTA"
  fi

  echo -e "   [${color}${http_code}${C_RESET}] (${duration_ms}ms) ${label:-$path}"
}

# Worker loop function
run_scenario_worker() {
  local wid="$1"
  local end_time=$(( $(date +%s) + DURATION ))

  while [ $(date +%s) -lt $end_time ]; do
    case "$SCENARIO" in
    mixed)
      # =======================================================================
      # 10+ SIMULTANEOUS ISSUES GENERATOR:
      #  1.  🔴 Critical: Django 500 on /api/products/error/
      #  2.  🔴 Critical: Django 500 on /api/template-error/
      #  3.  🔴 Critical: Upstream 502 Bad Gateway on /api/external/
      #  4.  🔴 Critical: Django 500 on /api/products/1/error/
      #  5.  🔴 Critical: PostgreSQL Database query failure / SQL Exception
      #  6.  🔴 Critical: Multi-DB database connection failure
      #  7.  🔴 Critical: Bad payload / validation crash on /api/products/bulk_create/
      #  8.  🟡 Warning:  PostgreSQL Primary high latency bottleneck (>30% time)
      #  9.  🟡 Warning:  PostgreSQL Slave1 high latency bottleneck
      #  10. 🟡 Warning:  Redis cache slow key scanning (>20% time)
      #  11. 🟡 Warning:  Django internal compute latency bottleneck (>800ms)
      #  12. ℹ️ Info:     Traffic Surge Anomaly on /api/products/
      #  13. ℹ️ Info:     Traffic Surge Anomaly on /api/products/health/
      # =======================================================================

      # --- 1-4. Multiple Endpoint 5xx Failures ---
      send_req "GET" "/api/products/error/" "" "🔴 [Issue 1] Django: 500 RuntimeError Spike"
      send_req "GET" "/api/template-error/" "" "🔴 [Issue 2] Django: 500 Multi-Part Template Crash"
      send_req "GET" "/api/external/?url=http://127.0.0.1:59999/broken" "" "🔴 [Issue 3] External: 502 Upstream Failure"
      send_req "GET" "/api/products/1/error/" "" "🔴 [Issue 4] Django: 500 Product Detail Crash"

      # --- 5-7. Database & Validation Failures ---
      send_req "GET" "/api/raw-sql/?query=SELECT%20*%20FROM%20corrupted_nonexistent_table" "" "🔴 [Issue 5] Postgres: SQL Syntax/Table Failure"
      send_req "GET" "/api/multi-db/?db=corrupted_slave_down" "" "🔴 [Issue 6] Multi-DB: DB Connection Failure"
      send_req "POST" "/api/products/bulk_create/" '{"invalid_structure": true}' "🔴 [Issue 7] Django: Validation Error"

      # --- 8-11. Latency Bottlenecks ---
      send_req "GET" "/api/products/postgres-slow/?delay=${DELAY}" "" "🟡 [Issue 8] Postgres (default): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/products/postgres-slow/?db=slave1&delay=${DELAY}" "" "🟡 [Issue 9] Postgres (slave1): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/products/redis-slow/?delay=1.6" "" "🟡 [Issue 10] Redis: Cache Scan Latency (1.6s delay)"
      send_req "GET" "/api/products/slow/?delay=1.8" "" "🟡 [Issue 11] Django Internal: Compute Delay (1.8s delay)"

      # --- 12-13. Traffic Surges & Normal Baselines ---
      send_req "GET" "/api/products/" "" "ℹ️ [Issue 12] Django: Product Catalog Traffic Surge"
      send_req "GET" "/api/products/health/" "" "ℹ️ [Issue 13] Health: Health Ping Traffic Surge"
      send_req "GET" "/api/cache-stats/" "" "Django: Cache Stats"
      send_req "GET" "/api/s3-storage/" "" "Storage: S3 Storage Ping"
      ;;

    postgres)
      # Injects real slow PostgreSQL queries across DBs
      send_req "GET" "/api/products/postgres-slow/?delay=${DELAY}" "" "PostgreSQL (default): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/products/postgres-slow/?db=slave1&delay=${DELAY}" "" "PostgreSQL (slave1): Slow Query (${DELAY}s delay)"
      send_req "GET" "/api/raw-sql/?query=SELECT%20pg_sleep(${DELAY})" "" "PostgreSQL: Raw SQL pg_sleep(${DELAY}s)"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    redis)
      # Injects slow Redis cache operations
      send_req "GET" "/api/products/redis-slow/?delay=${DELAY}" "" "Redis: Heavy Key Scan / Iteration Delay (${DELAY}s)"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats & Memory Info"
      send_req "GET" "/api/products/" "" "Django: Product Catalog View"
      ;;

    internal)
      # Injects pure internal Django execution latency
      send_req "GET" "/api/products/slow/?delay=${DELAY}" "" "Django Internal: Simulated Compute / Handler Delay (${DELAY}s)"
      send_req "GET" "/api/products-tmpl/" "" "Django Internal: Template Rendering Loop"
      ;;

    error)
      # Generates target error rate percentage across multiple endpoints
      local roll=$(( RANDOM % 100 + 1 ))
      if [ "$roll" -le "$ERROR_RATE" ]; then
        local err_type=$(( RANDOM % 4 ))
        case $err_type in
        0) send_req "GET" "/api/products/error/" "" "Django: 500 RuntimeError Spike" ;;
        1) send_req "GET" "/api/template-error/" "" "Django: Multi-Part Template Rendering Crash" ;;
        2) send_req "GET" "/api/external/?url=http://127.0.0.1:59999/broken" "" "External: 502 Upstream Failure" ;;
        3) send_req "GET" "/api/products/1/error/" "" "Django: 500 Product Detail Error" ;;
        esac
      else
        send_req "GET" "/api/products/" "" "Django: Products List (200 OK)"
      fi
      ;;

    db-error)
      # Injects SQL exceptions and broken database connections
      send_req "GET" "/api/raw-sql/?query=SELECT%20*%20FROM%20dead_corrupted_table" "" "Postgres: SQL Execution Exception"
      send_req "GET" "/api/multi-db/?db=corrupted_slave" "" "Multi-DB: Database Connection Failure"
      send_req "GET" "/api/products/postgres-slow/?delay=1.0" "" "Postgres: Normal DB Query"
      ;;

    traffic)
      # Floods fast endpoints with high request rate
      send_req "GET" "/api/products/" "" "Django: High Throughput Traffic Surge"
      send_req "GET" "/api/products/health/" "" "Health: Multi-Service Health Ping"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats Surge"
      ;;

    chaos)
      # Aggressive chaos across all subsystems
      send_req "GET" "/api/products/postgres-slow/?delay=${DELAY}" "" "PostgreSQL: Slow Query (Chaos)"
      send_req "GET" "/api/products/redis-slow/?delay=${DELAY}" "" "Redis: Slow Cache (Chaos)"
      send_req "GET" "/api/products/slow/?delay=${DELAY}" "" "Django: Internal Slow (Chaos)"
      send_req "GET" "/api/products/error/" "" "Django: 500 Error (Chaos)"
      send_req "GET" "/api/template-error/" "" "Django: Template Crash (Chaos)"
      send_req "GET" "/api/raw-sql/?query=SELECT%20*%20FROM%20nonexistent_chaos_table" "" "Postgres: SQL Error (Chaos)"
      send_req "GET" "/api/products/" "" "Django: Product List (Chaos)"
      ;;

    healthy)
      # Fast, standard healthy traffic with normal latency and 0 errors
      send_req "GET" "/api/products/" "" "Django: Products List (Healthy)"
      send_req "GET" "/api/products/read-slave1/" "" "Postgres: Slave1 Read (Healthy)"
      send_req "GET" "/api/cache-stats/" "" "Redis: Cache Stats (Healthy)"
      send_req "GET" "/api/products/health/" "" "Health: Service Check (Healthy)"
      ;;
    esac

    [ "$(echo "$SLEEP_DELAY > 0" | bc 2>/dev/null || echo 1)" = "1" ] && sleep "$SLEEP_DELAY"
  done
}

echo -e "${C_YELLOW}🚀 Starting anomaly injection for ${DURATION}s... (Press Ctrl+C to abort)${C_RESET}"
echo ""

# Launch concurrent workers
PIDS=()
for ((w = 1; w <= CONCURRENCY; w++)); do
  run_scenario_worker "$w" &
  PIDS+=($!)
done

# Wait for all workers to complete
trap 'kill "${PIDS[@]}" 2>/dev/null || true; exit 1' INT TERM
wait "${PIDS[@]}" 2>/dev/null || true

echo ""
echo -e "${C_GREEN}${C_BOLD}✔ Anomaly generation cycle complete!${C_RESET}"
echo -e "Open the Grafana dashboards to view all detected issues:"
echo -e "  ${C_CYAN}Needs Attention Table:${C_RESET} http://localhost:3000/d/tp_trace-needs-attention"
echo -e "  ${C_CYAN}Service Catalog Cards:${C_RESET} http://localhost:3000/d/tp_trace-project-catalog"
