from __future__ import annotations

from typing import Any


class _GuardedMCPSession:
    """Proxy session that enforces tool policy before calling call_tool()."""

    def __init__(self, session: Any, wrapper: Any) -> None:
        # Keep references to the original session and the wrapper policy hooks.
        self._session = session
        self._wrapper = wrapper

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        # Extract arguments first so the wrapper can inspect and log the request consistently.
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        # Enforce the wrapper policy before the underlying MCP session executes the tool.
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        # Forward the tool call to the original session once the policy check passes.
        result = await self._session.call_tool(name, *args, **kwargs)
        # Wrap the raw tool result so downstream guardrails and redaction rules still apply.
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        # Delegate every other attribute access to the wrapped session transparently.
        return getattr(self._session, item)


class _GuardedMCPClient:
    """Proxy client that wraps sessions and enforces tool policy."""

    def __init__(self, client: Any, wrapper: Any) -> None:
        # Keep references to the original client and the wrapper policy hooks.
        self._client = client
        self._wrapper = wrapper

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        # Wrap each created session so per-tool policy enforcement also happens inside sessions.
        session = await self._client.get_session(*args, **kwargs)
        return _GuardedMCPSession(session, self._wrapper)

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        # Extract arguments first so the wrapper can inspect and log the request consistently.
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        # Enforce the wrapper policy before the underlying MCP client executes the tool.
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        # Forward the tool call to the original client once the policy check passes.
        result = await self._client.call_tool(name, *args, **kwargs)
        # Wrap the raw tool result so downstream guardrails and redaction rules still apply.
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        # Delegate every other attribute access to the wrapped client transparently.
        return getattr(self._client, item)
