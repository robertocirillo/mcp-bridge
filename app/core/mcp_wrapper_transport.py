from __future__ import annotations

from typing import Any


class _GuardedMCPSession:
    """Proxy session that enforces tool policy before calling call_tool()."""

    def __init__(self, session: Any, wrapper: Any) -> None:
        self._session = session
        self._wrapper = wrapper

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        result = await self._session.call_tool(name, *args, **kwargs)
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._session, item)


class _GuardedMCPClient:
    """Proxy client that wraps sessions and enforces tool policy."""

    def __init__(self, client: Any, wrapper: Any) -> None:
        self._client = client
        self._wrapper = wrapper

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        session = await self._client.get_session(*args, **kwargs)
        return _GuardedMCPSession(session, self._wrapper)

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        result = await self._client.call_tool(name, *args, **kwargs)
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._client, item)
