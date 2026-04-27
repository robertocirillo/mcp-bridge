#!/usr/bin/env bash
set -euo pipefail

MCP_BRIDGE_BASE_URL="${MCP_BRIDGE_BASE_URL:-http://localhost:8000}"
MCP_BRIDGE_BASE_URL="${MCP_BRIDGE_BASE_URL%/}"
MCP_BRIDGE_LLM_PROVIDER="${MCP_BRIDGE_LLM_PROVIDER:-openai}"
MCP_BRIDGE_LLM_MODEL="${MCP_BRIDGE_LLM_MODEL:-gpt-4o-mini}"
MCP_SERVER_ROOT="${MCP_SERVER_ROOT:-/tmp}"
MCP_BRIDGE_TENANT_ID="${MCP_BRIDGE_TENANT_ID:-}"
MCP_BRIDGE_RUN_ID="${MCP_BRIDGE_RUN_ID:-}"

SESSION_ID=""
API_BODY=""
API_STATUS=""

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Error: required command not found: %s\n' "$1" >&2
    exit 1
  fi
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
  local body_file=""
  local status

  body_file="$(mktemp)"
  local curl_args=(
    curl
    --silent
    --show-error
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

  if ! status="$("${curl_args[@]}")"; then
    rm -f "$body_file"
    printf 'Error: request failed: %s %s\n' "$method" "$path" >&2
    return 1
  fi

  API_BODY="$(cat "$body_file")"
  API_STATUS="$status"
  rm -f "$body_file"

  if [[ ! "$API_STATUS" =~ ^[0-9]{3}$ ]]; then
    printf 'Error: unexpected HTTP status for %s %s: %s\n' "$method" "$path" "$API_STATUS" >&2
    return 1
  fi

  if (( API_STATUS < 200 || API_STATUS >= 300 )); then
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

  print_json "$API_BODY"
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

  if [[ ! -d "$MCP_SERVER_ROOT" ]]; then
    printf 'Error: MCP_SERVER_ROOT does not exist or is not a directory: %s\n' "$MCP_SERVER_ROOT" >&2
    exit 1
  fi

  query_text="Use the filesystem MCP tools to list the files in ${query_target}."

  log_step "Demo configuration"
  log_info "Base URL: ${MCP_BRIDGE_BASE_URL}"
  log_info "LLM provider: ${MCP_BRIDGE_LLM_PROVIDER}"
  log_info "LLM model: ${MCP_BRIDGE_LLM_MODEL}"
  log_info "Filesystem root: ${MCP_SERVER_ROOT}"
  log_info "MCP server command: npx -y @modelcontextprotocol/server-filesystem ${MCP_SERVER_ROOT}"

  log_step "Health check"
  api_request "GET" "/health"
  print_json "$API_BODY"

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
  api_request "POST" "/sessions" "$session_payload"
  print_json "$API_BODY"

  SESSION_ID="$(printf '%s\n' "$API_BODY" | jq -r '.session_id')"
  if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
    printf 'Error: session_id not found in POST /sessions response\n' >&2
    exit 1
  fi

  log_info "Session ID: ${SESSION_ID}"

  log_step "Run query"
  query_payload="$(
    jq -n \
      --arg query "$query_text" \
      '{
        query: $query,
        max_steps: 10
      }'
  )"
  api_request "POST" "/sessions/${SESSION_ID}/query" "$query_payload"

  log_info "Query response:"
  printf '\n%s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.result')"
  printf '\nserver_used: %s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.server_used // "n/a"')"
  printf 'execution_time: %s seconds\n' "$(printf '%s\n' "$API_BODY" | jq -r '.execution_time')"
  printf 'steps_used: %s\n' "$(printf '%s\n' "$API_BODY" | jq -r '.steps_used')"
}

main "$@"
