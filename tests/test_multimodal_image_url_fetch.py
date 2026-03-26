import httpx
import pytest

from app.core.multimodal_image_fetch import (
    MAX_REMOTE_IMAGE_BYTES,
    REMOTE_IMAGE_FETCH_USER_AGENT,
    RemoteImageFetchError,
    RemoteImageFetcher,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _build_fetcher(handler) -> RemoteImageFetcher:
    transport = httpx.MockTransport(handler)
    return RemoteImageFetcher(
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(5.0),
            headers={
                "User-Agent": REMOTE_IMAGE_FETCH_USER_AGENT,
                "Accept": "image/*",
            },
        )
    )


@pytest.mark.asyncio
async def test_remote_image_fetcher_downloads_valid_png(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def handler(_request: httpx.Request) -> httpx.Response:
        captured_headers["user-agent"] = _request.headers.get("user-agent", "")
        captured_headers["accept"] = _request.headers.get("accept", "")
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
            content=PNG_BYTES,
        )

    fetcher = _build_fetcher(handler)

    async def _resolve_host_ips(_hostname: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)

    fetched = await fetcher.fetch("https://example.com/image.png")

    assert fetched.mime_type == "image/png"
    assert fetched.content == PNG_BYTES
    assert captured_headers["user-agent"] == REMOTE_IMAGE_FETCH_USER_AGENT
    assert captured_headers["accept"] == "image/*"


@pytest.mark.asyncio
async def test_default_client_factory_sets_non_default_user_agent():
    client = RemoteImageFetcher._default_client_factory()
    try:
        assert client.headers["User-Agent"] == REMOTE_IMAGE_FETCH_USER_AGENT
        assert client.headers["Accept"] == "image/*"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/image.png",
        "file:///tmp/image.png",
    ],
)
async def test_remote_image_fetcher_rejects_unsupported_schemes(url):
    fetcher = RemoteImageFetcher()

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch(url)

    assert "only http and https are allowed" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/image.png",
        "http://127.0.0.1/image.png",
        "https://10.0.0.5/image.png",
    ],
)
async def test_remote_image_fetcher_rejects_local_or_private_hosts(url):
    fetcher = RemoteImageFetcher()

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch(url)

    assert "host is not allowed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_image_fetcher_rejects_unsupported_mime(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/gif", "content-length": "6"},
            content=b"GIF89a",
        )

    fetcher = _build_fetcher(handler)

    async def _resolve_host_ips(_hostname: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch("https://example.com/image.gif")

    assert "MIME type is not supported" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_image_fetcher_rejects_body_over_limit(monkeypatch):
    oversized_png = PNG_BYTES + (b"\x00" * (MAX_REMOTE_IMAGE_BYTES + 1))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/png"},
            content=oversized_png,
        )

    fetcher = _build_fetcher(handler)

    async def _resolve_host_ips(_hostname: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch("https://example.com/image.png")

    assert "exceeds the maximum size" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_image_fetcher_rejects_timeout(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    fetcher = _build_fetcher(handler)

    async def _resolve_host_ips(_hostname: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch("https://example.com/image.png")

    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_image_fetcher_rejects_redirect_to_disallowed_host(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/image.png":
            return httpx.Response(status_code=302, headers={"location": "http://127.0.0.1/image.png"})
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
            content=PNG_BYTES,
        )

    fetcher = _build_fetcher(handler)

    async def _resolve_host_ips(hostname: str) -> set[str]:
        if hostname == "example.com":
            return {"93.184.216.34"}
        return {"127.0.0.1"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)

    with pytest.raises(RemoteImageFetchError) as exc_info:
        await fetcher.fetch("https://example.com/image.png")

    assert "host is not allowed" in str(exc_info.value)
