"""End-to-end TLS tests for direct (unproxied) connections on both backends.

``tests/test_proxy.py`` covers the TLS settings that apply to the *proxy*
connection; these cover the ones that apply to the endpoint itself — a custom CA
bundle, ``verify=False``, and a client certificate — against a real HTTPS server
whose certificate is minted by ``trustme``.
"""

from __future__ import annotations

import json
import ssl

import anyio
import pytest
import trustme
from anyio.abc import SocketAttribute
from anyio.streams.tls import TLSListener
from botocore.awsrequest import AWSRequest
from botocore.exceptions import SSLError

pytestmark = pytest.mark.anyio

TARGET_HOST = "localhost"
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
        with anyio.CancelScope(shield=True):
            try:
                await stream.aclose()
            except anyio.BrokenResourceError:  # pragma: no cover
                # The client hung up on us mid-handshake (verification failed).
                pass


async def _serve_https_target(
    ca, *, hostname=TARGET_HOST, client_ca=None, task_status
) -> None:
    server_cert = ca.issue_cert(hostname)
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(ssl_context)
    if client_ca is not None:
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        client_ca.configure_trust(ssl_context)

    listener = await anyio.create_tcp_listener(
        local_host="127.0.0.1", local_port=0
    )
    port = listener.listeners[0].extra(SocketAttribute.local_port)
    task_status.started(port)
    await TLSListener(listener, ssl_context).serve(_handle_target)


@pytest.fixture
def ca():
    return trustme.CA()


@pytest.fixture
def ca_bundle(ca, tmp_path):
    path = tmp_path / "ca.pem"
    path.write_bytes(ca.cert_pem.bytes())
    return str(path)


def _prepared_request(port: int, host: str = TARGET_HOST) -> AWSRequest:
    request = AWSRequest(
        method="GET",
        url=f"https://{host}:{port}/foo?id=1",
        headers={"Accept": "application/json"},
    ).prepare()
    request.stream_output = False
    return request


async def test_custom_ca_bundle(http_session_cls, ca, ca_bundle):
    # The endpoint's verify setting has to reach the connection: this CA is not
    # in certifi, so the handshake only succeeds if the bundle is honored.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(verify=ca_bundle) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_untrusted_ca_is_rejected(http_session_cls, ca):
    # Same server, but verify defaults to the system trust store.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls() as session:
            with pytest.raises(SSLError):
                await session.send(_prepared_request(target_port))

        tg.cancel_scope.cancel()


async def test_verify_false_skips_verification(http_session_cls, ca):
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(_serve_https_target, ca)

        async with http_session_cls(verify=False) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200

        tg.cancel_scope.cancel()


async def test_client_cert_with_verify_false(http_session_cls, ca, tmp_path):
    # A server that demands a client certificate: the cert has to be presented
    # even though the client isn't verifying the server.
    leaf = ca.issue_cert("client@example.com")
    pem_path = tmp_path / "client-combined.pem"
    pem_path.write_bytes(
        b"".join(b.bytes() for b in leaf.cert_chain_pems)
        + leaf.private_key_pem.bytes()
    )

    async with anyio.create_task_group() as tg:
        target_port = await tg.start(
            lambda *, task_status: _serve_https_target(
                ca, client_ca=ca, task_status=task_status
            )
        )

        async with http_session_cls(
            verify=False, client_cert=str(pem_path)
        ) as session:
            response = await session.send(_prepared_request(target_port))
            assert response.status_code == 200

        tg.cancel_scope.cancel()


async def test_hostname_mismatch_is_rejected(http_session_cls, ca, ca_bundle):
    # The certificate is valid and signed by the trusted CA, but issued for a
    # different host than the one being connected to.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(
            lambda *, task_status: _serve_https_target(
                ca, hostname="wrong.example.com", task_status=task_status
            )
        )

        async with http_session_cls(verify=ca_bundle) as session:
            with pytest.raises(SSLError):
                await session.send(_prepared_request(target_port))

        tg.cancel_scope.cancel()
