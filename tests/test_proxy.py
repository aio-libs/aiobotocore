"""End-to-end proxy tests for both http backends.

These stand up a real HTTP ``CONNECT`` proxy (``tiny_proxy``) in front of a real
HTTPS target whose certificate is minted by ``trustme``, then drive the session
through it. They are parametrized over the http backend, so aiohttp's and
httpx's proxy code are both exercised against a real proxy (aiohttp on asyncio,
httpx on asyncio and trio).
"""

from __future__ import annotations

import json
import ssl
import sys

import anyio
import pytest
import tiny_proxy
import trustme
from anyio.abc import SocketAttribute
from anyio.streams.tls import TLSListener
from botocore.awsrequest import AWSRequest
from botocore.exceptions import (
    HTTPClientError,
    InvalidProxiesConfigError,
    ProxyConnectionError,
)

pytestmark = pytest.mark.anyio

TARGET_HOST = "localhost"
PROXY_HOST = "localhost"
RESPONSE_BODY = b'{"ok": true}'


async def _handle_target(stream) -> None:
    """A minimal HTTP/1.1 server: read one request, return a fixed 200."""
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += await stream.receive()
        await stream.send(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: %d\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n%b" % (len(RESPONSE_BODY), RESPONSE_BODY)
        )
    except (anyio.EndOfStream, anyio.BrokenResourceError):  # pragma: no cover
        pass
    finally:
        await stream.aclose()


async def _serve_https_target(ca, *, task_status) -> None:
    server_cert = ca.issue_cert(TARGET_HOST)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(ssl_context)

    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1", local_port=0
    )
    port = listener.extra(SocketAttribute.local_port)
    task_status.started(port)
    await TLSListener(listener, ssl_context).serve(_handle_target)


async def _serve_http_proxy(*, task_status) -> None:
    handler = tiny_proxy.HttpProxyHandler()
    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1", local_port=0
    )
    port = listener.extra(SocketAttribute.local_port)
    task_status.started(port)
    await listener.serve(handler.handle)


async def _serve_https_proxy(ca, *, client_ca=None, task_status) -> None:
    # A hostname (not an IP) so _setup_proxy_ssl_context enables hostname
    # checking against proxy_ca_bundle.
    proxy_cert = ca.issue_cert(PROXY_HOST)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    proxy_cert.configure_cert(ssl_context)
    if client_ca is not None:
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        client_ca.configure_trust(ssl_context)

    handler = tiny_proxy.HttpProxyHandler()
    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1", local_port=0
    )
    port = listener.extra(SocketAttribute.local_port)
    task_status.started(port)
    await TLSListener(listener, ssl_context).serve(handler.handle)


@pytest.fixture
def ca():
    return trustme.CA()


@pytest.fixture
def ca_bundle(ca, tmp_path):
    path = tmp_path / "ca.pem"
    path.write_bytes(ca.cert_pem.bytes())
    return str(path)


@pytest.fixture
def proxy_client_cert(ca, tmp_path):
    leaf = ca.issue_cert("client@example.com")
    cert_path = tmp_path / "client.pem"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(b"".join(b.bytes() for b in leaf.cert_chain_pems))
    key_path.write_bytes(leaf.private_key_pem.bytes())
    return str(cert_path), str(key_path)


@pytest.fixture
def proxy_client_cert_combined(ca, tmp_path):
    leaf = ca.issue_cert("client@example.com")
    pem_path = tmp_path / "client-combined.pem"
    pem_path.write_bytes(
        b"".join(b.bytes() for b in leaf.cert_chain_pems)
        + leaf.private_key_pem.bytes()
    )
    return str(pem_path)


@pytest.fixture(params=["string", "tuple"])
def client_cert(request, ca, tmp_path):
    leaf = ca.issue_cert("client@example.com")
    cert_path = tmp_path / "client.pem"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(b"".join(b.bytes() for b in leaf.cert_chain_pems))
    key_path.write_bytes(leaf.private_key_pem.bytes())
    if request.param == "string":
        pem_path = tmp_path / "client-combined.pem"
        pem_path.write_bytes(cert_path.read_bytes() + key_path.read_bytes())
        return str(pem_path)
    return str(cert_path), str(key_path)


def _prepared_request(port: int) -> AWSRequest:
    request = AWSRequest(
        method="GET",
        url=f"https://{TARGET_HOST}:{port}/foo?id=1",
        headers={"Accept": "application/json"},
    ).prepare()
    request.stream_output = False
    return request


@pytest.mark.parametrize("add_host_header", [False, True])
async def test_https_request_through_http_proxy(
    http_session_cls, ca, ca_bundle, monkeypatch, add_host_header
):
    if add_host_header:
        monkeypatch.setenv("BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER", "true")

    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_http_proxy)
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"http://127.0.0.1:{proxy_port}"},
            verify=ca_bundle,
        ) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_https_request_through_https_proxy(
    http_session_cls, current_http_backend, ca, ca_bundle, proxy_client_cert
):
    # An https:// proxy exercises _setup_proxy_ssl_context: the client must
    # complete a TLS handshake with the proxy (verified against proxy_ca_bundle,
    # and loading proxy_client_cert) before the CONNECT tunnel to the target.
    if current_http_backend == "aiohttp" and sys.version_info < (3, 11):
        # An https target through an https proxy is TLS-in-TLS, which stdlib
        # asyncio (and therefore aiohttp) can't do before Python 3.11. httpx
        # tunnels through httpcore and is unaffected.
        pytest.skip("aiohttp TLS-in-TLS requires Python 3.11+")

    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_https_proxy, ca)
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={
                "proxy_ca_bundle": ca_bundle,
                "proxy_client_cert": proxy_client_cert,
            },
            verify=ca_bundle,
        ) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_https_request_through_https_proxy_with_combined_proxy_client_cert(
    http_session_cls,
    current_http_backend,
    ca,
    ca_bundle,
    proxy_client_cert_combined,
):
    if current_http_backend == "aiohttp" and sys.version_info < (3, 11):
        pytest.skip("aiohttp TLS-in-TLS requires Python 3.11+")

    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_https_proxy, ca)
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={
                "proxy_ca_bundle": ca_bundle,
                "proxy_client_cert": proxy_client_cert_combined,
            },
            verify=ca_bundle,
        ) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_https_request_through_http_proxy_with_client_cert(
    http_session_cls, ca, ca_bundle, client_cert
):
    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_http_proxy)
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"http://127.0.0.1:{proxy_port}"},
            verify=ca_bundle,
            client_cert=client_cert,
        ) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_endpoint_client_cert_is_not_offered_to_proxy(
    http_session_cls, ca, ca_bundle, client_cert
):
    # The endpoint's client certificate is for the endpoint only: urllib3
    # passes cert_file=None for the proxy handshake, so a proxy demanding a
    # client certificate must not be satisfied by it.
    if sys.version_info < (3, 11):
        pytest.skip("aiohttp TLS-in-TLS requires Python 3.11+")

    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(
            lambda *, task_status: _serve_https_proxy(
                ca, client_ca=ca, task_status=task_status
            )
        )
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={"proxy_ca_bundle": ca_bundle},
            verify=ca_bundle,
            client_cert=client_cert,
        ) as session:
            with pytest.raises((ProxyConnectionError, HTTPClientError)):
                await session.send(_prepared_request(target_port))

        tg.cancel_scope.cancel()


async def test_invalid_proxy_ca_bundle(http_session_cls, tmp_path):
    # A non-existent proxy_ca_bundle fails while building the proxy SSL context
    # (no network needed). httpx builds it on __aenter__ and raises
    # InvalidProxiesConfigError directly; aiohttp builds it lazily inside send,
    # so the error is wrapped in HTTPClientError.
    missing = str(tmp_path / "missing.pem")
    with pytest.raises((InvalidProxiesConfigError, HTTPClientError)):
        async with http_session_cls(
            proxies={"https": "https://localhost:1"},
            proxies_config={"proxy_ca_bundle": missing},
        ) as session:
            await session.send(_prepared_request(1))


async def test_proxy_cannot_reach_target(http_session_cls, ca_bundle):
    # The proxy is up, but the CONNECT target is a closed port, so the proxy
    # replies with an error status — surfaced as ProxyConnectionError.
    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_http_proxy)

        async with http_session_cls(
            proxies={"https": f"http://127.0.0.1:{proxy_port}"},
            verify=ca_bundle,
        ) as session:
            with pytest.raises(ProxyConnectionError):
                await session.send(_prepared_request(1))

        tg.cancel_scope.cancel()
