"""End-to-end TLS tests for direct (unproxied) connections on both backends.

``tests/test_proxy.py`` covers the TLS settings that apply to the *proxy*
connection; these cover the ones that apply to the endpoint itself — a custom CA
bundle, ``verify=False``, and a client certificate — against a real HTTPS server
whose certificate is minted by ``trustme``.
"""

from __future__ import annotations

import json

import anyio
import pytest
from botocore.exceptions import SSLError
from tests.tls_helpers import (
    prepared_request,
    serve_https_target,
)

pytestmark = pytest.mark.anyio

async def test_custom_ca_bundle(http_session_cls, ca, ca_bundle):
    # The endpoint's verify setting has to reach the connection: this CA is not
    # in certifi, so the handshake only succeeds if the bundle is honored.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(verify=ca_bundle) as session:
            response = await session.send(prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_untrusted_ca_is_rejected(http_session_cls, ca):
    # Same server, but verify defaults to the system trust store.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls() as session:
            with pytest.raises(SSLError):
                await session.send(prepared_request(target_port))

        tg.cancel_scope.cancel()


async def test_verify_false_skips_verification(http_session_cls, ca):
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(verify=False) as session:
            response = await session.send(prepared_request(target_port))
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
            lambda *, task_status: serve_https_target(
                ca, client_ca=ca, task_status=task_status
            )
        )

        async with http_session_cls(
            verify=False, client_cert=str(pem_path)
        ) as session:
            response = await session.send(prepared_request(target_port))
            assert response.status_code == 200

        tg.cancel_scope.cancel()


async def test_hostname_mismatch_is_rejected(http_session_cls, ca, ca_bundle):
    # The certificate is valid and signed by the trusted CA, but issued for a
    # different host than the one being connected to.
    async with anyio.create_task_group() as tg:
        target_port = await tg.start(
            lambda *, task_status: serve_https_target(
                ca, hostname="wrong.example.com", task_status=task_status
            )
        )

        async with http_session_cls(verify=ca_bundle) as session:
            with pytest.raises(SSLError):
                await session.send(prepared_request(target_port))

        tg.cancel_scope.cancel()
