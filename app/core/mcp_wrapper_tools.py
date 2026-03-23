from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import BaseModel, ConfigDict

from app.core.exceptions import (
    ConfigurationError,
    MCPCapabilityError,
    MCPCapabilityNotSupportedError,
    MCPWrapperError,
    QueryOperationElicitationDeclinedError,
)
from app.core.mcp_wrapper_errors import GuardrailViolationError, MCPToolNotAllowedError
from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.core.mcp_wrapper import MCPWrapper


logger = get_logger(__name__)


class _RawMCPRequest(BaseModel):
    """Minimal JSON-RPC request envelope for MCP methods not modeled by the Python SDK."""

    method: str
    params: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(extra="allow")


class _RawMCPResult(BaseModel):
    """Generic response model for raw MCP requests with task payloads."""

    model_config = ConfigDict(extra="allow")


def _extract_tool_input_schema(wrapper: MCPWrapper, tool: Any) -> Optional[Dict[str, Any]]:
    schema = wrapper._extract_nested_value(tool, "inputSchema")
    return schema if isinstance(schema, dict) else None


def validate_task_tool_arguments(
    wrapper: MCPWrapper,
    *,
    tool_name: str,
    tool_definition: Any,
    arguments: Dict[str, Any],
) -> None:
    input_schema = _extract_tool_input_schema(wrapper, tool_definition)
    if input_schema is None:
        return

    try:
        from jsonschema import SchemaError, ValidationError, validate

        validate(instance=arguments or {}, schema=input_schema)
    except ValidationError as exc:
        raise ValueError(f"Invalid tool arguments for '{tool_name}': {exc.message}") from exc
    except SchemaError:
        logger.warning(
            "Skipping local inputSchema validation for tool '%s' due to invalid upstream schema",
            tool_name,
        )


def extract_tool_task_support(wrapper: MCPWrapper, tool: Any) -> Optional[str]:
    execution = wrapper._extract_nested_value(tool, "execution")
    if execution is None:
        meta = wrapper._extract_nested_value(tool, "_meta")
        execution = wrapper._extract_nested_value(meta, "execution")
    task_support = wrapper._extract_nested_value(execution, "taskSupport")
    if task_support is None:
        task_support = wrapper._extract_nested_value(execution, "task_support")
    return str(task_support).strip().lower() if task_support is not None else None


def extract_task_request_capability(wrapper: MCPWrapper, capabilities: Any) -> Optional[bool]:
    task_call_support = wrapper._extract_nested_value(
        capabilities,
        "tasks",
        "requests",
        "tools",
        "call",
    )
    if task_call_support is None:
        return None
    if isinstance(task_call_support, bool):
        return task_call_support
    if isinstance(task_call_support, dict):
        return True
    dumped = wrapper._coerce_mapping(task_call_support)
    if dumped is not None:
        return True
    return bool(task_call_support)


def _unwrap_capability_session(session: Any) -> Any:
    return getattr(session, "_session", session)


def get_protocol_client_session(wrapper: MCPWrapper, session: Any) -> Any:
    raw_session = _unwrap_capability_session(session)
    connector = getattr(raw_session, "connector", None)
    if connector is None:
        return None
    return getattr(connector, "client_session", None)


def get_server_capabilities(wrapper: MCPWrapper, session: Any) -> Any:
    raw_session = _unwrap_capability_session(session)
    connector = getattr(raw_session, "connector", None)
    capabilities = getattr(connector, "capabilities", None)
    if capabilities is not None:
        return capabilities
    session_info = getattr(raw_session, "session_info", None)
    if isinstance(session_info, dict):
        return session_info.get("capabilities")
    return wrapper._extract_nested_value(session_info, "capabilities")


async def get_tool_definition(
    wrapper: MCPWrapper,
    *,
    session: Any,
    server_name: str,
    tool_name: str,
) -> Any:
    try:
        tools_result = await wrapper._invoke_capability_method(
            session,
            operation="list_tools",
            method_names=["list_tools"],
            call_variants=[((), {})],
            server_name=server_name,
        )
    except MCPCapabilityError:
        logger.info(
            "mcp_task_support_detection server=%s tool=%s list_tools=unavailable",
            server_name,
            tool_name,
        )
        return None

    tools: Any = tools_result if isinstance(tools_result, list) else wrapper._extract_nested_value(tools_result, "tools")
    if not isinstance(tools, list):
        logger.info(
            "mcp_task_support_detection server=%s tool=%s list_tools_type=%s extracted_tools_type=%s",
            server_name,
            tool_name,
            type(tools_result).__name__,
            type(tools).__name__ if tools is not None else "None",
        )
        return None
    logger.info(
        "mcp_task_support_detection server=%s tool=%s list_tools_type=%s tools_count=%s",
        server_name,
        tool_name,
        type(tools_result).__name__,
        len(tools),
    )
    for tool in tools:
        if wrapper._extract_nested_value(tool, "name") == tool_name:
            logger.info(
                "mcp_task_support_detection server=%s tool=%s matched=true task_support=%s",
                server_name,
                tool_name,
                extract_tool_task_support(wrapper, tool),
            )
            return tool
    logger.info(
        "mcp_task_support_detection server=%s tool=%s matched=false",
        server_name,
        tool_name,
    )
    return None


async def send_raw_mcp_request(
    wrapper: MCPWrapper,
    *,
    client_session: Any,
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if client_session is None or not hasattr(client_session, "send_request"):
        raise MCPCapabilityNotSupportedError(
            "task_transport",
            "MCP runtime does not expose a raw client session for task-based tool execution",
            server_name=wrapper._active_server_name,
        )

    logger.info(
        "mcp_task_transport_request server=%s method=%s params_keys=%s",
        wrapper._active_server_name,
        method,
        sorted((params or {}).keys()),
    )
    request = _RawMCPRequest(method=method, params=params)
    try:
        result = await client_session.send_request(request, _RawMCPResult)
    except Exception as exc:
        error_payload = getattr(exc, "error", None)
        logger.warning(
            "mcp_task_transport_error server=%s method=%s code=%s message=%s data=%s",
            wrapper._active_server_name,
            method,
            getattr(error_payload, "code", None),
            getattr(error_payload, "message", str(exc)),
            wrapper._truncate_log_value(getattr(error_payload, "data", None)),
        )
        raise
    dumped = result.model_dump(by_alias=True, exclude_none=False)
    logger.info(
        "mcp_task_transport_response server=%s method=%s has_task=%s result_keys=%s",
        wrapper._active_server_name,
        method,
        isinstance(dumped.get("task"), dict) if isinstance(dumped, dict) else False,
        sorted(dumped.keys()) if isinstance(dumped, dict) else [],
    )
    logger.debug(
        "mcp_task_transport_payload server=%s method=%s payload=%s",
        wrapper._active_server_name,
        method,
        wrapper._truncate_log_value(dumped),
    )
    return dumped if isinstance(dumped, dict) else {}


def coerce_call_tool_result(result: Dict[str, Any]) -> Any:
    try:
        from mcp.types import CallToolResult

        return CallToolResult.model_validate(result)
    except Exception:
        return result


async def call_tool_with_task_support(
    wrapper: MCPWrapper,
    *,
    session: Any,
    tool_definition: Any,
    tool_name: str,
    arguments: Dict[str, Any],
    server_name: str,
) -> Any:
    wrapper._enforce_tool_allowed(tool_name, arguments)
    validate_task_tool_arguments(
        wrapper,
        tool_name=tool_name,
        tool_definition=tool_definition,
        arguments=arguments,
    )

    client_session = get_protocol_client_session(wrapper, session)
    capabilities = get_server_capabilities(wrapper, session)
    task_capability = extract_task_request_capability(wrapper, capabilities)
    if task_capability is False:
        raise MCPCapabilityNotSupportedError(
            "task_transport",
            (
                f"Tool '{tool_name}' requires task augmentation, but server '{server_name}' "
                "did not advertise tasks.requests.tools.call support"
            ),
            server_name=server_name,
        )
    logger.info(
        "mcp_task_transport_selected server=%s tool=%s task_capability=%s",
        server_name,
        tool_name,
        task_capability,
    )

    create_result = await send_raw_mcp_request(
        wrapper,
        client_session=client_session,
        method="tools/call",
        params={
            "name": tool_name,
            "arguments": arguments or {},
            "task": {},
        },
    )
    task_payload = wrapper._extract_nested_value(create_result, "task")
    if not isinstance(task_payload, dict):
        logger.info(
            "mcp_task_transport_fallback server=%s tool=%s reason=missing_task_payload",
            server_name,
            tool_name,
        )
        direct_result = coerce_call_tool_result(create_result)
        return wrapper._wrap_tool_result(tool_name, direct_result, arguments=arguments)

    task_id = wrapper._extract_nested_value(task_payload, "taskId")
    if task_id is None:
        raise MCPWrapperError(
            f"Task-aware tool '{tool_name}' did not return a taskId in the create-task response"
        )
    logger.info(
        "mcp_task_transport_created server=%s tool=%s task_id=%s",
        server_name,
        tool_name,
        task_id,
    )

    task_context = wrapper._get_operation_context_snapshot()
    if task_context is not None:
        wrapper._task_operation_contexts[str(task_id)] = task_context

    try:
        result_payload = await send_raw_mcp_request(
            wrapper,
            client_session=client_session,
            method="tasks/result",
            params={"taskId": str(task_id)},
        )
    except Exception:
        last_action = wrapper._task_operation_contexts.get(str(task_id), {}).get("last_elicitation_action")
        if last_action == "cancel":
            raise asyncio.CancelledError()
        if last_action == "decline":
            raise QueryOperationElicitationDeclinedError(
                f"Elicitation declined for task-aware tool '{tool_name}'"
            )
        raise
    else:
        last_action = wrapper._task_operation_contexts.get(str(task_id), {}).get("last_elicitation_action")
        if last_action == "cancel":
            raise asyncio.CancelledError()
        if last_action == "decline":
            raise QueryOperationElicitationDeclinedError(
                f"Elicitation declined for task-aware tool '{tool_name}'"
            )

        tool_result = coerce_call_tool_result(result_payload)
        return wrapper._wrap_tool_result(tool_name, tool_result, arguments=arguments)
    finally:
        wrapper._task_operation_contexts.pop(str(task_id), None)


async def call_tool(
    wrapper: MCPWrapper,
    tool_name: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
    server_name: Optional[str] = None,
) -> Any:
    if not wrapper._initialized:
        await wrapper.initialize()

    tool_arguments = dict(arguments or {})
    resolved_server_name = wrapper._resolve_capability_server_name(server_name)
    previous_active_server_name = getattr(wrapper, "_active_server_name", None)
    wrapper._active_server_name = resolved_server_name

    try:
        task_session = await wrapper._get_capability_session(resolved_server_name)
        tool_definition = await get_tool_definition(
            wrapper,
            session=task_session,
            server_name=resolved_server_name,
            tool_name=tool_name,
        )
        task_support = extract_tool_task_support(wrapper, tool_definition)
        logger.info(
            "mcp_tool_execution_path server=%s tool=%s task_support=%s branch=%s",
            resolved_server_name,
            tool_name,
            task_support,
            "task-aware" if task_support == "required" else "standard",
        )

        if task_support == "required":
            result = await call_tool_with_task_support(
                wrapper,
                session=task_session,
                tool_definition=tool_definition,
                tool_name=tool_name,
                arguments=tool_arguments,
                server_name=resolved_server_name,
            )
        else:
            result = await wrapper._invoke_capability_method(
                task_session,
                operation="call_tool",
                method_names=["call_tool"],
                call_variants=[
                    ((tool_name,), {"arguments": tool_arguments}),
                    ((tool_name, tool_arguments), {}),
                    ((), {"name": tool_name, "arguments": tool_arguments}),
                    ((), {"tool_name": tool_name, "arguments": tool_arguments}),
                ],
                server_name=resolved_server_name,
            )
        wrapper._steps_used = 0
        wrapper._last_server_used = resolved_server_name
        wrapper._record_audit_event(
            event_type="tool_execution",
            outcome="completed",
            tool_name=tool_name,
            details={
                "server_name": resolved_server_name,
                "arguments_present": bool(tool_arguments),
            },
        )
        return result
    except MCPToolNotAllowedError:
        wrapper._record_audit_event(
            event_type="tool_execution",
            outcome="blocked",
            tool_name=tool_name,
            details={"reason": "tool_policy", "server_name": resolved_server_name},
        )
        raise
    except GuardrailViolationError:
        wrapper._record_audit_event(
            event_type="tool_execution",
            outcome="blocked",
            tool_name=tool_name,
            details={"reason": "guardrail", "server_name": resolved_server_name},
        )
        raise
    except (ConfigurationError, MCPWrapperError, QueryOperationElicitationDeclinedError, ValueError):
        raise
    except Exception as exc:
        logger.error("Tool execution error: %s", exc)
        raise MCPWrapperError(f"Tool execution failed: {exc}")
    finally:
        wrapper._active_server_name = previous_active_server_name
