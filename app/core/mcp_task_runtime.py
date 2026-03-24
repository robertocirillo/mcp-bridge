from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anyio

import mcp.types as types
from mcp.client.session import ClientSession as BaseClientSession
from mcp.shared.message import SessionMessage
from mcp.shared.session import RequestResponder
from mcp.types import (
    CONNECTION_CLOSED,
    INVALID_PARAMS,
    CancelledNotification,
    ClientResult,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    ProgressNotification,
)

logger = logging.getLogger(__name__)


@dataclass
class BridgeTaskStatusNotification:
    method: str
    params: dict[str, Any]
    metadata: Any = None


class BridgeAwareClientSession(BaseClientSession):
    """ClientSession variant that forwards tasks/status notifications to the bridge."""

    async def _receive_loop(self) -> None:
        async with (
            self._read_stream,
            self._write_stream,
        ):
            try:
                async for message in self._read_stream:
                    if isinstance(message, Exception):
                        await self._handle_incoming(message)
                    elif isinstance(message.message.root, JSONRPCRequest):
                        try:
                            validated_request = self._receive_request_type.model_validate(
                                message.message.root.model_dump(by_alias=True, mode="json", exclude_none=True)
                            )
                            responder = RequestResponder(
                                request_id=message.message.root.id,
                                request_meta=validated_request.root.params.meta
                                if validated_request.root.params
                                else None,
                                request=validated_request,
                                session=self,
                                on_complete=lambda r: self._in_flight.pop(r.request_id, None),
                                message_metadata=message.metadata,
                            )
                            self._in_flight[responder.request_id] = responder
                            await self._received_request(responder)

                            if not responder._completed:  # type: ignore[reportPrivateUsage]
                                await self._handle_incoming(responder)
                        except Exception as e:
                            logging.warning(f"Failed to validate request: {e}")
                            logging.debug(f"Message that failed validation: {message.message.root}")
                            error_response = JSONRPCError(
                                jsonrpc="2.0",
                                id=message.message.root.id,
                                error=ErrorData(
                                    code=INVALID_PARAMS,
                                    message="Invalid request parameters",
                                    data="",
                                ),
                            )
                            session_message = SessionMessage(message=JSONRPCMessage(error_response))
                            await self._write_stream.send(session_message)

                    elif isinstance(message.message.root, JSONRPCNotification):
                        raw_notification = message.message.root.model_dump(by_alias=True, mode="json", exclude_none=True)
                        if raw_notification.get("method") == "notifications/tasks/status":
                            params = raw_notification.get("params") or {}
                            await self._handle_incoming(
                                BridgeTaskStatusNotification(
                                    method="notifications/tasks/status",
                                    params=params if isinstance(params, dict) else {},
                                    metadata=message.metadata,
                                )
                            )
                            continue

                        try:
                            notification = self._receive_notification_type.model_validate(raw_notification)
                            if isinstance(notification.root, CancelledNotification):
                                cancelled_id = notification.root.params.requestId
                                if cancelled_id in self._in_flight:
                                    await self._in_flight[cancelled_id].cancel()
                            else:
                                if isinstance(notification.root, ProgressNotification):
                                    progress_token = notification.root.params.progressToken
                                    if progress_token in self._progress_callbacks:
                                        callback = self._progress_callbacks[progress_token]
                                        try:
                                            await callback(
                                                notification.root.params.progress,
                                                notification.root.params.total,
                                                notification.root.params.message,
                                            )
                                        except Exception as e:
                                            logging.error("Progress callback raised an exception: %s", e)
                                await self._received_notification(notification)
                                await self._handle_incoming(notification)
                        except Exception as e:
                            logging.warning(
                                f"Failed to validate notification: {e}. Message was: {message.message.root}"
                            )
                    else:
                        stream = self._response_streams.pop(message.message.root.id, None)
                        if stream:
                            await stream.send(message.message.root)
                        else:
                            await self._handle_incoming(
                                RuntimeError(f"Received response with an unknown request ID: {message}")
                            )

            except anyio.ClosedResourceError:
                logging.debug("Read stream closed by client")
            except Exception as e:
                logging.exception(f"Unhandled exception in receive loop: {e}")
            finally:
                for request_id, stream in self._response_streams.items():
                    error = ErrorData(code=CONNECTION_CLOSED, message="Connection closed")
                    try:
                        await stream.send(JSONRPCError(jsonrpc="2.0", id=request_id, error=error))
                        await stream.aclose()
                    except Exception:
                        pass
                self._response_streams.clear()


_RUNTIME_PATCHED = False


async def _populate_http_connector_capabilities(connector: Any, initialize_result: Any) -> None:
    connector.capabilities = initialize_result.capabilities
    connector._initialized = True

    server_capabilities = initialize_result.capabilities

    if server_capabilities.tools:
        tools_result = await connector.client_session.list_tools()
        connector._tools = tools_result.tools if tools_result else []
    else:
        connector._tools = []

    if server_capabilities.resources:
        resources_result = await connector.client_session.list_resources()
        connector._resources = resources_result.resources if resources_result else []
    else:
        connector._resources = []

    if server_capabilities.prompts:
        prompts_result = await connector.client_session.list_prompts()
        connector._prompts = prompts_result.prompts if prompts_result else []
    else:
        connector._prompts = []


async def _cleanup_failed_http_connection(connection_manager: Any, client_session: Any = None) -> None:
    if client_session is not None:
        try:
            await client_session.__aexit__(None, None, None)
        except Exception:
            pass

    if connection_manager is None:
        return

    try:
        await connection_manager.stop()
    except Exception:
        pass


def _normalize_http_transport(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip().lower()
    if normalized in {"streamable-http", "streamable_http"}:
        return "streamable-http"
    if normalized == "sse":
        return "sse"
    return None


async def _connect_streamable_http_only(connector: Any, http_module: Any) -> Any:
    connection_manager = http_module.StreamableHttpConnectionManager(
        connector.base_url,
        connector.headers,
        connector.timeout,
        connector.sse_read_timeout,
    )
    client_session = None

    try:
        read_stream, write_stream = await connection_manager.start()
        client_session = http_module.ClientSession(
            read_stream,
            write_stream,
            sampling_callback=connector.sampling_callback,
            elicitation_callback=connector.elicitation_callback,
            message_handler=connector._internal_message_handler,
            logging_callback=connector.logging_callback,
            client_info=connector.client_info,
        )
        await client_session.__aenter__()
        initialize_result = await client_session.initialize()
        connector.client_session = client_session
        connector.transport_type = "streamable HTTP"
        await _populate_http_connector_capabilities(connector, initialize_result)
        return connection_manager
    except Exception:
        await _cleanup_failed_http_connection(connection_manager, client_session)
        raise


async def _connect_sse_only(connector: Any, http_module: Any) -> Any:
    connection_manager = http_module.SseConnectionManager(
        connector.base_url,
        connector.headers,
        connector.timeout,
        connector.sse_read_timeout,
    )
    client_session = None

    try:
        read_stream, write_stream = await connection_manager.start()
        client_session = http_module.ClientSession(
            read_stream,
            write_stream,
            sampling_callback=connector.sampling_callback,
            elicitation_callback=connector.elicitation_callback,
            message_handler=connector._internal_message_handler,
            logging_callback=connector.logging_callback,
            client_info=connector.client_info,
        )
        await client_session.__aenter__()
        connector.client_session = client_session
        connector.transport_type = "SSE"
        return connection_manager
    except Exception:
        await _cleanup_failed_http_connection(connection_manager, client_session)
        raise


def install_task_notification_runtime_patch() -> None:
    """Patch mcp-use connectors to use the bridge-aware ClientSession."""

    global _RUNTIME_PATCHED
    if _RUNTIME_PATCHED:
        return

    import mcp
    import mcp_use.client as client_module
    import mcp_use.config as config_module
    import mcp_use.connectors.base as connectors_base
    import mcp_use.connectors.http as connectors_http
    import mcp_use.connectors.stdio as connectors_stdio

    mcp.ClientSession = BridgeAwareClientSession
    connectors_base.ClientSession = BridgeAwareClientSession
    connectors_stdio.ClientSession = BridgeAwareClientSession
    connectors_http.ClientSession = BridgeAwareClientSession

    original_http_connect = getattr(connectors_http.HttpConnector, "_bridge_original_connect", None)
    if original_http_connect is None:
        original_http_connect = connectors_http.HttpConnector.connect
        connectors_http.HttpConnector._bridge_original_connect = original_http_connect

    async def _bridge_http_connect(self: Any) -> None:
        transport_hint = _normalize_http_transport(getattr(self, "_bridge_transport", None))
        if transport_hint is None:
            await original_http_connect(self)
            return

        if self._connected:
            connectors_http.logger.debug("Already connected to MCP implementation")
            return

        if transport_hint == "streamable-http":
            connection_manager = await _connect_streamable_http_only(self, connectors_http)
        else:
            connection_manager = await _connect_sse_only(self, connectors_http)

        self._connection_manager = connection_manager
        self._connected = True
        connectors_http.logger.debug(
            "Successfully connected to MCP implementation via %s: %s",
            self.transport_type,
            self.base_url,
        )

    connectors_http.HttpConnector.connect = _bridge_http_connect

    original_create_connector = getattr(config_module, "_bridge_original_create_connector_from_config", None)
    if original_create_connector is None:
        original_create_connector = config_module.create_connector_from_config
        config_module._bridge_original_create_connector_from_config = original_create_connector

    def _bridge_create_connector_from_config(*args: Any, **kwargs: Any) -> Any:
        connector = original_create_connector(*args, **kwargs)
        server_config = args[0] if args else kwargs.get("server_config")
        transport_hint = None
        if isinstance(server_config, dict):
            transport_hint = _normalize_http_transport(server_config.get("transport"))
        if transport_hint is not None and isinstance(connector, connectors_http.HttpConnector):
            connector._bridge_transport = transport_hint
        return connector

    config_module.create_connector_from_config = _bridge_create_connector_from_config
    client_module.create_connector_from_config = _bridge_create_connector_from_config

    for module_name in (
        "mcp_use.connectors.websocket",
        "mcp_use.connectors.sandbox",
    ):
        try:
            module = __import__(module_name, fromlist=["ClientSession"])
            setattr(module, "ClientSession", BridgeAwareClientSession)
        except Exception:
            continue

    _RUNTIME_PATCHED = True
