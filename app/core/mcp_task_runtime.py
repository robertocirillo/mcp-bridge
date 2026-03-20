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


def install_task_notification_runtime_patch() -> None:
    """Patch mcp-use connectors to use the bridge-aware ClientSession."""

    global _RUNTIME_PATCHED
    if _RUNTIME_PATCHED:
        return

    import mcp
    import mcp_use.connectors.base as connectors_base
    import mcp_use.connectors.stdio as connectors_stdio

    mcp.ClientSession = BridgeAwareClientSession
    connectors_base.ClientSession = BridgeAwareClientSession
    connectors_stdio.ClientSession = BridgeAwareClientSession

    for module_name in (
        "mcp_use.connectors.http",
        "mcp_use.connectors.websocket",
        "mcp_use.connectors.sandbox",
    ):
        try:
            module = __import__(module_name, fromlist=["ClientSession"])
            setattr(module, "ClientSession", BridgeAwareClientSession)
        except Exception:
            continue

    _RUNTIME_PATCHED = True
