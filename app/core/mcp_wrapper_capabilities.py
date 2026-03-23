from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.exceptions import (
    MCPCapabilityNotSupportedError,
    MCPCapabilityUpstreamError,
    MCPWrapperError,
    QueryOperationElicitationDeclinedError,
)
from app.core.mcp_wrapper_transport import _GuardedMCPSession
from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.core.mcp_wrapper import MCPWrapper


logger = get_logger(__name__)

CapabilityCallVariant = tuple[tuple[Any, ...], Dict[str, Any]]


async def await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def is_signature_compatible(
    method: Any,
    args: tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Optional[bool]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return None

    try:
        signature.bind(*args, **kwargs)
    except TypeError:
        return False
    return True


def type_error_entered_callable(method: Any, exc: TypeError) -> bool:
    target = getattr(method, "__func__", method)
    target_code = getattr(target, "__code__", None)
    if target_code is None:
        return False

    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is target_code:
            return True
        tb = tb.tb_next
    return False


def resolve_invocation_target(container: Any, method_name: str, fallback: Any) -> Any:
    saw_wrapped_target = False
    for attr_name in ("_session", "_client"):
        inner = getattr(container, attr_name, None)
        if inner is None:
            continue
        saw_wrapped_target = True
        target = getattr(inner, method_name, None)
        if target is not None:
            return target
    if saw_wrapped_target:
        return None
    return fallback


def wrap_capability_session(session: Any, wrapper: MCPWrapper) -> Any:
    if isinstance(session, _GuardedMCPSession):
        return session
    return _GuardedMCPSession(session, wrapper)


def is_missing_session_error(exc: Exception, server_name: str) -> bool:
    message = str(exc).strip().lower()
    server_token = server_name.strip().lower()
    return (
        "no session exists" in message
        or "no active session" in message
        or ("session" in message and "not found" in message and server_token in message)
    ) and server_token in message


async def invoke_optional_client_method(
    wrapper: MCPWrapper,
    client: Any,
    *,
    method_name: str,
    call_variants: List[CapabilityCallVariant],
) -> Any:
    method = getattr(client, method_name, None)
    if method is None:
        return None

    target_method = resolve_invocation_target(client, method_name, method)
    if target_method is None:
        return None

    signature_mismatch = False
    for args, kwargs in call_variants:
        compatibility = is_signature_compatible(target_method, args, kwargs)
        if compatibility is False:
            signature_mismatch = True
            continue
        try:
            return await await_if_needed(method(*args, **kwargs))
        except TypeError as exc:
            if compatibility is None and not type_error_entered_callable(target_method, exc):
                signature_mismatch = True
                continue
            raise

    if signature_mismatch:
        logger.debug("Skipping %s due to incompatible runtime signature", method_name)
    return None


async def lookup_capability_session(
    wrapper: MCPWrapper,
    client: Any,
    *,
    server_name: str,
    allow_missing: bool,
) -> Any:
    get_session = getattr(client, "get_session", None)
    if get_session is None:
        return client

    signature_mismatch = False
    attempts: List[CapabilityCallVariant] = [
        ((), {"server_name": server_name}),
        ((server_name,), {}),
        ((), {"name": server_name}),
    ]
    if len(wrapper.mcp_servers) == 1:
        attempts.append(((), {}))

    target_get_session = resolve_invocation_target(client, "get_session", get_session)
    for args, kwargs in attempts:
        compatibility = is_signature_compatible(target_get_session, args, kwargs)
        if compatibility is False:
            signature_mismatch = True
            continue
        try:
            session = await await_if_needed(get_session(*args, **kwargs))
            return wrap_capability_session(session, wrapper)
        except MCPCapabilityNotSupportedError as exc:
            raise MCPCapabilityNotSupportedError(
                "session_transport",
                str(exc),
                server_name=server_name,
            ) from exc
        except TypeError as exc:
            if compatibility is None and not type_error_entered_callable(target_get_session, exc):
                signature_mismatch = True
                continue
            raise MCPCapabilityUpstreamError(
                "session_transport",
                f"Unable to obtain MCP session for server '{server_name}': {exc}",
                server_name=server_name,
            ) from exc
        except Exception as exc:
            if allow_missing and is_missing_session_error(exc, server_name):
                return None
            raise MCPCapabilityUpstreamError(
                "session_transport",
                f"Unable to obtain MCP session for server '{server_name}': {exc}",
                server_name=server_name,
            ) from exc

    if signature_mismatch:
        raise MCPCapabilityNotSupportedError(
            "session_transport",
            (
                f"Unable to obtain MCP session for server '{server_name}': "
                "the MCP runtime exposes an incompatible get_session signature"
            ),
            server_name=server_name,
        )

    if allow_missing:
        return None

    raise MCPCapabilityUpstreamError(
        "session_transport",
        f"Unable to obtain MCP session for server '{server_name}'",
        server_name=server_name,
    )


async def create_capability_session(
    wrapper: MCPWrapper,
    client: Any,
    *,
    server_name: str,
) -> Any:
    created_session = await invoke_optional_client_method(
        wrapper,
        client,
        method_name="create_session",
        call_variants=[
            ((), {"server_name": server_name, "auto_initialize": True}),
            ((server_name,), {"auto_initialize": True}),
            ((), {"server_name": server_name}),
            ((server_name,), {}),
            ((), {"name": server_name, "auto_initialize": True}),
            ((), {"name": server_name}),
        ],
    )
    if created_session is not None:
        return wrap_capability_session(created_session, wrapper)

    await invoke_optional_client_method(
        wrapper,
        client,
        method_name="create_all_sessions",
        call_variants=[
            ((), {"auto_initialize": True}),
            ((), {}),
        ],
    )
    return await lookup_capability_session(
        wrapper,
        client,
        server_name=server_name,
        allow_missing=False,
    )


async def get_capability_session(wrapper: MCPWrapper, server_name: str) -> Any:
    if not wrapper._initialized:
        await wrapper.initialize()

    client = wrapper._client
    if client is None:
        raise MCPWrapperError("MCP client not initialized")

    get_session = getattr(client, "get_session", None)
    if get_session is None:
        return client

    active_sessions = await invoke_optional_client_method(
        wrapper,
        client,
        method_name="get_all_active_sessions",
        call_variants=[((), {})],
    )
    if isinstance(active_sessions, dict) and server_name in active_sessions:
        return wrap_capability_session(active_sessions[server_name], wrapper)

    session = await lookup_capability_session(
        wrapper,
        client,
        server_name=server_name,
        allow_missing=True,
    )
    if session is not None:
        return session

    try:
        return await create_capability_session(wrapper, client, server_name=server_name)
    except MCPCapabilityNotSupportedError:
        raise
    except MCPCapabilityUpstreamError:
        raise
    except Exception as exc:
        raise MCPCapabilityUpstreamError(
            "session_transport",
            f"Unable to initialize MCP session for server '{server_name}': {exc}",
            server_name=server_name,
        ) from exc


async def invoke_capability_method(
    wrapper: MCPWrapper,
    session: Any,
    *,
    operation: str,
    method_names: List[str],
    call_variants: List[CapabilityCallVariant],
    server_name: str,
) -> Any:
    found_method = False
    signature_mismatch = False

    for method_name in method_names:
        method = getattr(session, method_name, None)
        if method is None:
            continue

        target_method = resolve_invocation_target(session, method_name, method)
        if target_method is None:
            continue

        found_method = True
        for args, kwargs in call_variants:
            compatibility = is_signature_compatible(target_method, args, kwargs)
            if compatibility is False:
                signature_mismatch = True
                continue
            try:
                return await await_if_needed(method(*args, **kwargs))
            except MCPCapabilityNotSupportedError:
                break
            except QueryOperationElicitationDeclinedError:
                raise
            except TypeError as exc:
                if compatibility is None and not type_error_entered_callable(target_method, exc):
                    signature_mismatch = True
                    continue
                logger.exception(
                    "MCP capability invocation failed",
                    extra={
                        "operation": operation,
                        "server_name": server_name,
                        "method_name": method_name,
                    },
                )
                raise MCPCapabilityUpstreamError(
                    operation,
                    f"{operation} failed: {exc}",
                    server_name=server_name,
                ) from exc
            except Exception as exc:
                logger.exception(
                    "MCP capability invocation failed",
                    extra={
                        "operation": operation,
                        "server_name": server_name,
                        "method_name": method_name,
                    },
                )
                raise MCPCapabilityUpstreamError(
                    operation,
                    f"{operation} failed: {exc}",
                    server_name=server_name,
                ) from exc

    if not found_method:
        supported = ", ".join(method_names)
        raise MCPCapabilityNotSupportedError(
            operation,
            f"MCP runtime does not support {operation} (expected one of: {supported})",
            server_name=server_name,
        )

    if signature_mismatch:
        raise MCPCapabilityNotSupportedError(
            operation,
            f"MCP runtime does not support {operation} with a compatible method signature",
            server_name=server_name,
        )

    raise MCPCapabilityUpstreamError(
        operation,
        f"{operation} failed",
        server_name=server_name,
    )


async def run_capability_operation(
    wrapper: MCPWrapper,
    *,
    operation: str,
    method_names: List[str],
    call_variants: List[CapabilityCallVariant],
    server_name: Optional[str],
) -> Any:
    resolved_server_name = wrapper._resolve_capability_server_name(server_name)
    previous_active_server_name = getattr(wrapper, "_active_server_name", None)
    wrapper._active_server_name = resolved_server_name

    try:
        session = await get_capability_session(wrapper, resolved_server_name)
        result = await invoke_capability_method(
            wrapper,
            session,
            operation=operation,
            method_names=method_names,
            call_variants=call_variants,
            server_name=resolved_server_name,
        )
        wrapper._last_server_used = resolved_server_name
        return result
    finally:
        wrapper._active_server_name = previous_active_server_name


async def list_prompts(wrapper: MCPWrapper, server_name: Optional[str] = None) -> Any:
    return await run_capability_operation(
        wrapper,
        operation="list_prompts",
        method_names=["list_prompts"],
        call_variants=[((), {})],
        server_name=server_name,
    )


async def get_prompt(
    wrapper: MCPWrapper,
    prompt_name: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
    server_name: Optional[str] = None,
) -> Any:
    prompt_arguments = dict(arguments or {})
    return await run_capability_operation(
        wrapper,
        operation="get_prompt",
        method_names=["get_prompt", "render_prompt"],
        call_variants=[
            ((prompt_name,), {"arguments": prompt_arguments}),
            ((prompt_name, prompt_arguments), {}),
            ((), {"name": prompt_name, "arguments": prompt_arguments}),
            ((), {"prompt_name": prompt_name, "arguments": prompt_arguments}),
        ],
        server_name=server_name,
    )


async def render_prompt(
    wrapper: MCPWrapper,
    prompt_name: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
    server_name: Optional[str] = None,
) -> Any:
    return await get_prompt(
        wrapper,
        prompt_name,
        arguments=arguments,
        server_name=server_name,
    )


async def list_resources(wrapper: MCPWrapper, server_name: Optional[str] = None) -> Any:
    return await run_capability_operation(
        wrapper,
        operation="list_resources",
        method_names=["list_resources"],
        call_variants=[((), {})],
        server_name=server_name,
    )


async def read_resource(
    wrapper: MCPWrapper,
    uri: str,
    *,
    server_name: Optional[str] = None,
) -> Any:
    return await run_capability_operation(
        wrapper,
        operation="read_resource",
        method_names=["read_resource"],
        call_variants=[
            ((uri,), {}),
            ((), {"uri": uri}),
            ((), {"resource_uri": uri}),
        ],
        server_name=server_name,
    )
