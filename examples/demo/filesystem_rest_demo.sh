#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MCP_SERVER_ROOT="${SCRIPT_DIR}/sample-files"

MCP_BRIDGE_BASE_URL="${MCP_BRIDGE_BASE_URL:-http://localhost:8000}"
MCP_BRIDGE_BASE_URL="${MCP_BRIDGE_BASE_URL%/}"
MCP_BRIDGE_LLM_PROVIDER="${MCP_BRIDGE_LLM_PROVIDER:-ollama}"
MCP_BRIDGE_LLM_MODEL="${MCP_BRIDGE_LLM_MODEL:-llama3.2:latest}"
MCP_SERVER_ROOT="${MCP_SERVER_ROOT:-$DEFAULT_MCP_SERVER_ROOT}"
MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS="${MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS:-120}"
MCP_BRIDGE_TENANT_ID="${MCP_BRIDGE_TENANT_ID:-}"
MCP_BRIDGE_RUN_ID="${MCP_BRIDGE_RUN_ID:-}"

SESSION_ID=""
API_BODY=""
API_STATUS=""
API_ERROR_KIND=""
API_ERROR_MESSAGE=""

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Error: required command not found: %s\n' "$1" >&2
    exit 1
  fi
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

log_step() {
  printf '\n==> %s\n' "$1"
}

log_info() {
  printf '%s\n' "$1"
}

print_json() {
  printf '%s\n' "$1" | jq .
}

api_request() {
  local method="$1"
  local path="$2"
  local payload="${3-}"
  local timeout_seconds="${4:-20}"
  local body_file=""
  local status
  local curl_exit_code

  API_ERROR_KIND=""
  API_ERROR_MESSAGE=""
  body_file="$(mktemp)"
  local curl_args=(
    curl
    --silent
    --show-error
    --max-time
    "$timeout_seconds"
    --output
    "$body_file"
    --write-out
    "%{http_code}"
    --request
    "$method"
    --header
    "Accept: application/json"
  )

  if [[ -n "$MCP_BRIDGE_TENANT_ID" ]]; then
    curl_args+=(
      --header
      "X-Tenant-Id: $MCP_BRIDGE_TENANT_ID"
    )
  fi

  if [[ -n "$MCP_BRIDGE_RUN_ID" ]]; then
    curl_args+=(
      --header
      "X-Run-Id: $MCP_BRIDGE_RUN_ID"
    )
  fi

  if [[ -n "$payload" ]]; then
    curl_args+=(
      --header
      "Content-Type: application/json"
      --data
      "$payload"
    )
  fi

  curl_args+=("${MCP_BRIDGE_BASE_URL}${path}")

  if status="$("${curl_args[@]}")"; then
    :
  else
    curl_exit_code="$?"
    rm -f "$body_file"
    if [[ "$curl_exit_code" -eq 28 ]]; then
      API_ERROR_KIND="timeout"
      API_ERROR_MESSAGE="${method} ${path} timed out after ${timeout_seconds} seconds"
      printf 'Error: %s\n' "$API_ERROR_MESSAGE" >&2
    else
      API_ERROR_KIND="transport"
      API_ERROR_MESSAGE="request failed: ${method} ${path}"
      printf 'Error: %s\n' "$API_ERROR_MESSAGE" >&2
    fi
    return 1
  fi

  API_BODY="$(cat "$body_file")"
  API_STATUS="$status"
  rm -f "$body_file"

  if [[ ! "$API_STATUS" =~ ^[0-9]{3}$ ]]; then
    API_ERROR_KIND="invalid_status"
    API_ERROR_MESSAGE="unexpected HTTP status for ${method} ${path}: ${API_STATUS}"
    printf 'Error: unexpected HTTP status for %s %s: %s\n' "$method" "$path" "$API_STATUS" >&2
    return 1
  fi

  if (( API_STATUS < 200 || API_STATUS >= 300 )); then
    API_ERROR_KIND="http_error"
    API_ERROR_MESSAGE="${method} ${path} returned HTTP ${API_STATUS}"
    printf 'Error: %s %s returned HTTP %s\n' "$method" "$path" "$API_STATUS" >&2
    if [[ -n "$API_BODY" ]]; then
      printf '%s\n' "$API_BODY" | jq . 2>/dev/null || printf '%s\n' "$API_BODY" >&2
    fi
    return 1
  fi
}

delete_session() {
  local session_id="$1"

  if ! api_request "DELETE" "/sessions/${session_id}"; then
    printf 'Warning: failed to delete session %s\n' "$session_id" >&2
    return 1
  fi

  log_info "Session deleted: ${session_id}"
}

run_health_check() {
  local health_status
  local supported_providers

  if ! api_request "GET" "/health"; then
    die "mcp-bridge did not respond at ${MCP_BRIDGE_BASE_URL}. Start the service before running the demo."
  fi

  health_status="$(printf '%s\n' "$API_BODY" | jq -r '.status // empty')"
  if [[ "$health_status" != "healthy" ]]; then
    die "mcp-bridge responded but is not healthy. Response status: ${health_status:-unknown}"
  fi

  supported_providers="$(printf '%s\n' "$API_BODY" | jq -r '(.supported_providers // []) | join(", ")')"
  log_info "Bridge health: healthy"
  if [[ -n "$supported_providers" ]]; then
    log_info "Supported providers: ${supported_providers}"
  fi

  if ! printf '%s\n' "$API_BODY" | jq -e --arg provider "$MCP_BRIDGE_LLM_PROVIDER" '(.supported_providers // []) | index($provider) != null' >/dev/null; then
    die "Provider '${MCP_BRIDGE_LLM_PROVIDER}' is not advertised by GET /health. Adjust MCP_BRIDGE_LLM_PROVIDER or the bridge runtime configuration."
  fi

  if [[ "$MCP_BRIDGE_LLM_PROVIDER" == "ollama" ]]; then
    log_info "Ollama preflight: provider advertised by mcp-bridge"
  fi
}

cleanup() {
  local exit_code="$?"

  trap - EXIT INT TERM

  if [[ -n "$SESSION_ID" ]]; then
    log_step "Delete session"
    delete_session "$SESSION_ID" || true
  fi

  exit "$exit_code"
}

trap cleanup EXIT INT TERM

main() {
  local query_target="$MCP_SERVER_ROOT"
  local query_text
  local session_payload
  local query_payload

  require_command curl
  require_command jq
  require_command node
  require_command npx

  if [[ ! "$MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || (( MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS <= 0 )); then
    die "MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS must be a positive integer: ${MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS}"
  fi

  if [[ ! -d "$MCP_SERVER_ROOT" ]]; then
    die "MCP_SERVER_ROOT does not exist or is not a directory: ${MCP_SERVER_ROOT}"
  fi

  MCP_SERVER_ROOT="$(cd "$MCP_SERVER_ROOT" && pwd)"
  query_target="$MCP_SERVER_ROOT"
  query_text="Use the filesystem MCP tools to list the files in ${query_target} and briefly identify the sample files you find."

  log_step "Demo configuration"
  log_info "Base URL: ${MCP_BRIDGE_BASE_URL}"
  log_info "LLM provider: ${MCP_BRIDGE_LLM_PROVIDER}"
  log_info "LLM model: ${MCP_BRIDGE_LLM_MODEL}"
  log_info "Filesystem root: ${MCP_SERVER_ROOT}"
  log_info "Sync query timeout: ${MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS}s"
  log_info "MCP server: npx -y @modelcontextprotocol/server-filesystem ${MCP_SERVER_ROOT}"

  log_step "Health check"
  run_health_check

  log_step "Create session"
  session_payload="$(
    jq -n \
      --arg provider "$MCP_BRIDGE_LLM_PROVIDER" \
      --arg model "$MCP_BRIDGE_LLM_MODEL" \
      --arg root "$MCP_SERVER_ROOT" \
      '{
        llm_provider: {
          provider: $provider,
          model: $model,
          temperature: 0
        },
        mcp_servers: {
          filesystem: {
            command: "npx",
            args: ["-y", "@modelcontextprotocol/server-filesystem", $root]
          }
        }
      }'
  )"
  if ! api_request "POST" "/sessions" "$session_payload"; then
    die "session creation failed. Confirm that mcp-bridge can reach provider '${MCP_BRIDGE_LLM_PROVIDER}' with model '${MCP_BRIDGE_LLM_MODEL}'."
  fi

  SESSION_ID="$(printf '%s\n' "$API_BODY" | jq -r '.session_id')"
  if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
    die "session_id not found in POST /sessions response"
  fi

  log_info "Session created: ${SESSION_ID}"

  log_step "Run query"
  query_payload="$(
    jq -n \
      --arg query "$query_text" \
      '{
        query: $query,
        max_steps: 10
      }'
  )"
  if ! api_request "POST" "/sessions/${SESSION_ID}/query" "$query_payload" "$MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS"; then
    if [[ "$API_ERROR_KIND" == "timeout" ]]; then
      die "sync query timed out after ${MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS} seconds for session ${SESSION_ID}. Increase MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS for slower CPU-only Ollama demos."
    fi
    die "sync query failed for session ${SESSION_ID}. Confirm that the selected provider/model are reachable and that the filesystem MCP server can start."
  fi

  log_info "Query completed"
  printf '\n%s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.result')"
  printf '\nserver_used: %s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.server_used // "n/a"')"
  printf 'execution_time: %s seconds\n' "$(printf '%s\n' "$API_BODY" | jq -r '.execution_time')"
  printf 'steps_used: %s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.steps_used')"
}

main "$@"
