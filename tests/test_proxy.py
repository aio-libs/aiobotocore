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
from anyio.abc import SocketAttribute
from anyio.streams.tls import TLSListener
from botocore.exceptions import (
    HTTPClientError,
    InvalidProxiesConfigError,
    ProxyConnectionError,
)

from aiobotocore import httpxsession
from aiobotocore.httpxsession import HttpxSession
from tests.tls_helpers import (
    prepared_request,
    serve_https_target,
)

pytestmark = pytest.mark.anyio

PROXY_HOST = "localhost"


@pytest.mark.config_kwargs({'http_session_cls': HttpxSession})
async def test_httpx_entry_failure_closes_exit_stack(monkeypatch):
    class RecordingExitStack(httpxsession.AsyncExitStack):
        closed = False
        exit_exception = None

        async def __aexit__(self, exc_type, exc, traceback):
            self.closed = True
            self.exit_exception = exc
            return await super().__aexit__(exc_type, exc, traceback)

    exit_stack = RecordingExitStack()
    monkeypatch.setattr(httpxsession, 'AsyncExitStack', lambda: exit_stack)
    session = HttpxSession()

    def fail_build_ssl_contexts(_proxy_urls):
        raise RuntimeError('setup failed')

    monkeypatch.setattr(
        session, '_build_ssl_contexts', fail_build_ssl_contexts
    )

    with pytest.raises(RuntimeError, match='setup failed'):
        await session.__aenter__()

    assert exit_stack.closed
    assert isinstance(exit_stack.exit_exception, RuntimeError)
    assert session._entered is False


@pytest.mark.config_kwargs({'http_session_cls': HttpxSession})
def test_httpx_proxy_context_does_not_mutate_endpoint_context():
    endpoint_context = ssl.create_default_context()
    endpoint_context.check_hostname = False
    session = HttpxSession(
        proxies={'https': 'https://localhost:1234'},
        verify=endpoint_context,
    )

    verify, proxy_contexts = session._build_ssl_contexts(
        {'https': 'https://localhost:1234'}
    )

    proxy_context = proxy_contexts['https://localhost:1234']
    assert verify is endpoint_context
    assert proxy_context is not endpoint_context
    assert endpoint_context.check_hostname is False
    assert proxy_context.check_hostname is True
    assert set(proxy_context.get_ca_certs(binary_form=True)) == set(
        endpoint_context.get_ca_certs(binary_form=True)
    )


@pytest.mark.config_kwargs({'http_session_cls': HttpxSession})
def test_httpx_proxy_context_with_verification_disabled():
    endpoint_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    endpoint_context.check_hostname = False
    endpoint_context.verify_mode = ssl.CERT_NONE
    session = HttpxSession(
        proxies={'https': 'https://localhost:1234'},
        verify=endpoint_context,
    )

    verify, proxy_contexts = session._build_ssl_contexts(
        {'https': 'https://localhost:1234'}
    )

    proxy_context = proxy_contexts['https://localhost:1234']
    assert verify is endpoint_context
    assert proxy_context is not endpoint_context
    assert proxy_context.verify_mode == ssl.CERT_NONE
    assert proxy_context.check_hostname is False


@pytest.mark.config_kwargs({'http_session_cls': HttpxSession})
async def test_httpx_proxy_uses_one_client_for_multiple_targets(monkeypatch):
    async with HttpxSession(
        proxies={'https': 'http://127.0.0.1:1234'}
    ) as session:
        first = await session._get_session('https://first.example')
        second = await session._get_session('https://second.example')

        assert first is second
        assert len(session._sessions) == 1


@pytest.mark.config_kwargs({'http_session_cls': HttpxSession})
async def test_httpx_concurrent_requests_create_one_client(monkeypatch):
    async with HttpxSession() as session:
        client = session._make_async_client()
        entered = anyio.Event()
        release = anyio.Event()
        calls = 0

        async def enter_async_context(_client):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return client

        monkeypatch.setattr(
            session._exit_stack,
            'enter_async_context',
            enter_async_context,
        )
        results = []

        async def get_session(target):
            results.append(await session._get_session(target))

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(get_session, 'https://first.example')
            await entered.wait()
            task_group.start_soon(get_session, 'https://second.example')
            release.set()

        assert results == [client, client]
        assert calls == 1
        await client.aclose()


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


async def test_https_request_through_http_proxy(
    http_session_cls, ca, ca_bundle
):
    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_http_proxy)
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"http://127.0.0.1:{proxy_port}"},
            verify=ca_bundle,
        ) as session:
            response = await session.send(prepared_request(target_port))
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
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={
                "proxy_ca_bundle": ca_bundle,
                "proxy_client_cert": proxy_client_cert,
            },
            verify=ca_bundle,
        ) as session:
            response = await session.send(prepared_request(target_port))
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
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={
                "proxy_ca_bundle": ca_bundle,
                "proxy_client_cert": proxy_client_cert_combined,
            },
            verify=ca_bundle,
        ) as session:
            response = await session.send(prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

        tg.cancel_scope.cancel()


async def test_https_request_through_http_proxy_with_client_cert(
    http_session_cls, ca, ca_bundle, client_cert
):
    async with anyio.create_task_group() as tg:
        proxy_port = await tg.start(_serve_http_proxy)
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"http://127.0.0.1:{proxy_port}"},
            verify=ca_bundle,
            client_cert=client_cert,
        ) as session:
            response = await session.send(prepared_request(target_port))
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
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(
            proxies={"https": f"https://{PROXY_HOST}:{proxy_port}"},
            proxies_config={"proxy_ca_bundle": ca_bundle},
            verify=ca_bundle,
            client_cert=client_cert,
        ) as session:
            with pytest.raises((ProxyConnectionError, HTTPClientError)):
                await session.send(prepared_request(target_port))

        tg.cancel_scope.cancel()


async def test_environment_proxies_are_not_applied_by_the_backend(
    http_session_cls, ca, ca_bundle, monkeypatch
):
    # botocore resolves environment proxies itself — get_environ_proxies(), which
    # also honours system bypass settings — and passes the result to the session.
    # A backend that consults the environment on its own would proxy requests
    # botocore decided to send direct, e.g. an IMDS lookup covered by NO_PROXY.
    # Nothing is listening on the proxy port, so a request that used it fails.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")

    async with anyio.create_task_group() as tg:
        target_port = await tg.start(serve_https_target, ca)

        async with http_session_cls(verify=ca_bundle) as session:
            response = await session.send(prepared_request(target_port))
            assert response.status_code == 200
            assert json.loads(await response.content) == {"ok": True}

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
            await session.send(prepared_request(1))


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
                await session.send(prepared_request(1))

        tg.cancel_scope.cancel()
