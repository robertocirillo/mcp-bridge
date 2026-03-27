from __future__ import annotations

import inspect
from typing import Any

from app.core.exceptions import MCPCapabilityNotSupportedError


class _GuardedMCPSession:
    """Proxy session that enforces tool policy before calling call_tool()."""

    def __init__(self, session: Any, wrapper: Any) -> None:
        # Keep references to the original session and the wrapper policy hooks.
        self._session = session
        self._wrapper = wrapper

    async def _invoke_passthrough(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        # Forward non-tool MCP primitives while preserving async/sync compatibility.
        try:
            method = getattr(self._session, method_name)
        except AttributeError as exc:
            raise MCPCapabilityNotSupportedError(
                method_name,
                f"MCP runtime does not support {method_name}",
            ) from exc
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        # Extract arguments first so the wrapper can inspect and log the request consistently.
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        # Enforce the wrapper policy before the underlying MCP session executes the tool.
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        # Forward the tool call to the original session once the policy check passes.
        result = await self._session.call_tool(name, *args, **kwargs)
        # Wrap the raw tool result so downstream guardrails and redaction rules still apply.
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    async def list_prompts(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("list_prompts", *args, **kwargs)

    async def get_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("get_prompt", *args, **kwargs)

    async def render_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("render_prompt", *args, **kwargs)

    async def list_resources(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("list_resources", *args, **kwargs)

    async def read_resource(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("read_resource", *args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        # Delegate every other attribute access to the wrapped session transparently.
        return getattr(self._session, item)


class _GuardedMCPClient:
    """Proxy client that wraps sessions and enforces tool policy."""

    def __init__(self, client: Any, wrapper: Any) -> None:
        # Keep references to the original client and the wrapper policy hooks.
        self._client = client
        self._wrapper = wrapper

    async def _invoke_passthrough(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        # Forward non-tool MCP primitives while preserving async/sync compatibility.
        try:
            method = getattr(self._client, method_name)
        except AttributeError as exc:
            raise MCPCapabilityNotSupportedError(
                method_name,
                f"MCP runtime does not support {method_name}",
            ) from exc
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        # Wrap each created session so per-tool policy enforcement also happens inside sessions.
        try:
            session = await self._invoke_passthrough("get_session", *args, **kwargs)
        except AttributeError as exc:
            raise MCPCapabilityNotSupportedError(
                "session_transport",
                "MCP runtime does not support get_session",
            ) from exc
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

    async def list_prompts(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("list_prompts", *args, **kwargs)

    async def get_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("get_prompt", *args, **kwargs)

    async def render_prompt(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("render_prompt", *args, **kwargs)

    async def list_resources(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("list_resources", *args, **kwargs)

    async def read_resource(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke_passthrough("read_resource", *args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        # Delegate every other attribute access to the wrapped client transparently.
        return getattr(self._client, item)
