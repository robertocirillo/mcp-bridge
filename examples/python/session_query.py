#!/usr/bin/env python3

import json
import os
import sys
from urllib import error, request


BASE_URL = os.getenv("MCP_BRIDGE_BASE_URL", "http://localhost:8000").rstrip("/")
LLM_PROVIDER = os.getenv("MCP_BRIDGE_LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("MCP_BRIDGE_LLM_MODEL", "gpt-4o-mini")
SERVER_ROOT = os.getenv("MCP_SERVER_ROOT", "/tmp")
TENANT_ID = os.getenv("MCP_BRIDGE_TENANT_ID")
RUN_ID = os.getenv("MCP_BRIDGE_RUN_ID")


def build_headers():
    headers = {"Content-Type": "application/json"}
    if TENANT_ID:
        headers["X-Tenant-Id"] = TENANT_ID
    if RUN_ID:
        headers["X-Run-Id"] = RUN_ID
    return headers


def api_request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=build_headers(),
        method=method,
    )
    try:
        with request.urlopen(req) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {details}") from exc


def delete_session(session_id):
    try:
        api_request("DELETE", f"/sessions/{session_id}")
        print(f"\nDeleted session: {session_id}")
    except Exception as exc:  # Best-effort cleanup for example usage.
        print(f"\nWarning: failed to delete session {session_id}: {exc}", file=sys.stderr)


def main():
    session_id = None

    session_payload = {
        "llm_provider": {
            "provider": LLM_PROVIDER,
            "model": LLM_MODEL,
            "temperature": 0,
        },
        "mcp_servers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", SERVER_ROOT],
            }
        },
        "max_steps": 20,
    }

    query_payload = {
        "query": "Use the filesystem MCP server to list a few entries from the available root and summarize what is there.",
        "max_steps": 10,
    }

    try:
        session_response = api_request("POST", "/sessions", session_payload)
        session_id = session_response["session_id"]
        print(f"Created session: {session_id}")

        query_response = api_request("POST", f"/sessions/{session_id}/query", query_payload)
        print("\nQuery result:\n")
        print(query_response["result"])
    finally:
        if session_id:
            delete_session(session_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
