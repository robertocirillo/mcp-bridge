from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.multimodal.policy import MAX_REMOTE_IMAGE_BYTES
from app.core.multimodal.validation import (
    MultimodalInputValidationError,
    validate_supported_image_mime_type,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

IMAGE_FETCH_TIMEOUT_SECONDS = 5.0
MAX_REMOTE_IMAGE_REDIRECTS = 3
REMOTE_IMAGE_FETCH_USER_AGENT = "mcp-bridge/0.1 (+https://github.com/openai/codex)"

_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}


class RemoteImageFetchError(ValueError):
    """Raised when a remote image cannot be safely resolved."""


@dataclass(frozen=True)
class FetchedRemoteImage:
    url: str
    mime_type: str
    content: bytes
    content_length: Optional[int] = None


class RemoteImageFetcher:
    """Fetch remote images with basic SSRF hardening and size/MIME validation."""

    def __init__(
        self,
        *,
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client_factory

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: Optional[int] = None,
        max_bytes_scope: str = "single_image",
    ) -> FetchedRemoteImage:
        current_url = url
        redirects_followed = 0

        async with self._client_factory() as client:
            while True:
                await self._validate_target_url(current_url)

                try:
                    async with client.stream("GET", current_url, follow_redirects=False) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise RemoteImageFetchError(
                                    f"Image redirect target is missing for {self._redact_url(current_url)}"
                                )
                            redirects_followed += 1
                            if redirects_followed > MAX_REMOTE_IMAGE_REDIRECTS:
                                raise RemoteImageFetchError(
                                    "Image redirect limit exceeded "
                                    f"({MAX_REMOTE_IMAGE_REDIRECTS} hops)"
                                )

                            next_url = urljoin(str(response.request.url), location)
                            await self._validate_target_url(next_url)
                            logger.info(
                                "Following remote image redirect %s -> %s",
                                self._redact_url(current_url),
                                self._redact_url(next_url),
                            )
                            current_url = next_url
                            continue

                        if response.status_code >= 400:
                            raise RemoteImageFetchError(
                                "Image URL could not be reached "
                                f"(status {response.status_code}) for {self._redact_url(current_url)}"
                            )

                        mime_type = self._validate_content_type(response.headers.get("content-type"), current_url)
                        content_length = self._validate_content_length(
                            response.headers.get("content-length"),
                            current_url,
                            max_bytes=max_bytes,
                            max_bytes_scope=max_bytes_scope,
                        )
                        content = await self._read_bounded_body(
                            response,
                            current_url,
                            max_bytes=max_bytes,
                            max_bytes_scope=max_bytes_scope,
                        )

                except httpx.TimeoutException as exc:
                    raise RemoteImageFetchError(
                        f"Image download timed out for {self._redact_url(current_url)}"
                    ) from exc
                except httpx.RequestError as exc:
                    raise RemoteImageFetchError(
                        f"Image URL could not be reached for {self._redact_url(current_url)}"
                    ) from exc

                if not content:
                    raise RemoteImageFetchError(
                        f"Image download returned an empty body for {self._redact_url(current_url)}"
                    )

                detected_mime_type = self._detect_image_mime_type(content)
                if detected_mime_type is None:
                    raise RemoteImageFetchError(
                        f"Downloaded content is not a supported image for {self._redact_url(current_url)}"
                    )
                if detected_mime_type != mime_type:
                    raise RemoteImageFetchError(
                        "Image MIME type mismatch for "
                        f"{self._redact_url(current_url)}: declared {mime_type}, detected {detected_mime_type}"
                    )

                logger.info(
                    "Resolved remote image %s mime=%s bytes=%s",
                    self._redact_url(current_url),
                    mime_type,
                    len(content),
                )
                return FetchedRemoteImage(
                    url=current_url,
                    mime_type=mime_type,
                    content=content,
                    content_length=content_length,
                )

    @staticmethod
    def _default_client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(IMAGE_FETCH_TIMEOUT_SECONDS),
            headers={
                "User-Agent": REMOTE_IMAGE_FETCH_USER_AGENT,
                "Accept": "image/*",
            },
        )

    async def _validate_target_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise RemoteImageFetchError(
                f"Image URL scheme is not supported for {self._redact_url(url)}: only http and https are allowed"
            )
        if not parsed.hostname:
            raise RemoteImageFetchError(f"Image URL host is missing for {self._redact_url(url)}")
        if parsed.username or parsed.password:
            raise RemoteImageFetchError(
                f"Image URL credentials are not allowed for {self._redact_url(url)}"
            )

        hostname = parsed.hostname.strip().lower().rstrip(".")
        if hostname in _LOCAL_HOSTNAMES or hostname.endswith(".localhost"):
            raise RemoteImageFetchError(f"Image URL host is not allowed: {self._redact_url(url)}")

        resolved_ips = await self._resolve_host_ips(hostname)
        for ip_text in resolved_ips:
            ip = ipaddress.ip_address(ip_text)
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise RemoteImageFetchError(f"Image URL host is not allowed: {self._redact_url(url)}")

    async def _resolve_host_ips(self, hostname: str) -> set[str]:
        try:
            ipaddress.ip_address(hostname)
            return {hostname}
        except ValueError:
            pass

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise RemoteImageFetchError(
                f"Image URL host could not be resolved: {self._redact_url(f'https://{hostname}/')}"
            ) from exc

        addresses: set[str] = set()
        for family, _socktype, _proto, _canonname, sockaddr in infos:
            if family == socket.AF_INET6:
                addresses.add(sockaddr[0])
            elif family == socket.AF_INET:
                addresses.add(sockaddr[0])

        if not addresses:
            raise RemoteImageFetchError(
                f"Image URL host could not be resolved: {self._redact_url(f'https://{hostname}/')}"
            )
        return addresses

    @staticmethod
    def _validate_content_type(content_type: Optional[str], url: str) -> str:
        if not content_type:
            raise RemoteImageFetchError(
                f"Image response Content-Type is missing for {RemoteImageFetcher._redact_url(url)}"
            )

        try:
            return validate_supported_image_mime_type(
                content_type,
                context=RemoteImageFetcher._redact_url(url),
            )
        except MultimodalInputValidationError as exc:
            raise RemoteImageFetchError(str(exc)) from exc

    @staticmethod
    def _validate_content_length(
        content_length: Optional[str],
        url: str,
        *,
        max_bytes: Optional[int] = None,
        max_bytes_scope: str = "single_image",
    ) -> Optional[int]:
        if not content_length:
            return None

        try:
            content_length_value = int(content_length)
        except ValueError as exc:
            raise RemoteImageFetchError(
                f"Image response Content-Length is invalid for {RemoteImageFetcher._redact_url(url)}"
            ) from exc

        if content_length_value < 0:
            raise RemoteImageFetchError(
                f"Image response Content-Length is invalid for {RemoteImageFetcher._redact_url(url)}"
            )
        byte_limit = RemoteImageFetcher._effective_max_bytes(max_bytes)
        if content_length_value > byte_limit:
            raise RemoteImageFetchError(
                RemoteImageFetcher._build_size_error_message(
                    url,
                    byte_limit=byte_limit,
                    max_bytes=max_bytes,
                    max_bytes_scope=max_bytes_scope,
                )
            )
        return content_length_value

    @staticmethod
    async def _read_bounded_body(
        response: httpx.Response,
        url: str,
        *,
        max_bytes: Optional[int] = None,
        max_bytes_scope: str = "single_image",
    ) -> bytes:
        body = bytearray()
        byte_limit = RemoteImageFetcher._effective_max_bytes(max_bytes)
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > byte_limit:
                raise RemoteImageFetchError(
                    RemoteImageFetcher._build_size_error_message(
                        url,
                        byte_limit=byte_limit,
                        max_bytes=max_bytes,
                        max_bytes_scope=max_bytes_scope,
                    )
                )
        return bytes(body)

    @staticmethod
    def _effective_max_bytes(max_bytes: Optional[int]) -> int:
        if max_bytes is None:
            return MAX_REMOTE_IMAGE_BYTES
        return min(MAX_REMOTE_IMAGE_BYTES, max(0, max_bytes))

    @staticmethod
    def _build_size_error_message(
        url: str,
        *,
        byte_limit: int,
        max_bytes: Optional[int],
        max_bytes_scope: str,
    ) -> str:
        if max_bytes is not None and byte_limit < MAX_REMOTE_IMAGE_BYTES and max_bytes_scope == "request_budget":
            return (
                "Multimodal input images exceed the remaining request image budget "
                f"of {byte_limit} bytes while resolving {RemoteImageFetcher._redact_url(url)}"
            )
        return (
            "Image download exceeds the maximum size "
            f"of {byte_limit} bytes for {RemoteImageFetcher._redact_url(url)}"
        )

    @staticmethod
    def _detect_image_mime_type(content: bytes) -> Optional[str]:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        return None

    @staticmethod
    def _redact_url(url: str) -> str:
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return "[redacted-url]"
        return f"{parsed.scheme}://{parsed.netloc}/..."
