from __future__ import annotations

import asyncio
import io
import os
import socket
import ssl
from collections.abc import AsyncIterable, Iterable
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING, Any, cast

import botocore
from botocore.awsrequest import AWSPreparedRequest
from botocore.httpsession import (
    MAX_POOL_CONNECTIONS,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    InvalidProxiesConfigError,
    LocationParseError,
    ProxyConfiguration,
    ProxyConnectionError,
    ReadTimeoutError,
    _is_ipaddress,
    create_urllib3_context,
    ensure_boolean,
    get_cert_path,
    logger,
    mask_proxy_url,
    parse_url,
    urlparse,
)
from multidict import CIMultiDict

import aiobotocore.awsrequest
from aiobotocore._endpoint_helpers import _text

from ._constants import DEFAULT_KEEPALIVE_TIMEOUT

try:
    # anyio is a hard dependency of httpx, so it is importable whenever httpx is.
    # config.py imports this module unconditionally, so these imports must stay optional.
    import anyio.to_thread
    import httpx
except ImportError:
    httpx = None

if TYPE_CHECKING:
    from ssl import SSLContext


def _find_ssl_error(exc: BaseException) -> ssl.SSLError | None:
    """Find an ``ssl.SSLError`` in ``exc``'s cause/context chain.

    A failed TLS handshake reaches us as
    ``httpx.ConnectError -> httpcore.ConnectError -> ssl.SSLError``, linked by
    ``__cause__`` then ``__context__``, so both links are followed rather than
    assuming a fixed depth.
    """
    seen: set[int] = set()
    unvisited: list[BaseException | None] = [exc]
    while unvisited:
        current = unvisited.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return current
        unvisited += [current.__cause__, current.__context__]
    return None


class HttpxSession:
    def __init__(
        self,
        verify: bool = True,
        proxies: dict[str, str] | None = None,  # {scheme: url}
        timeout: float | list[float] | tuple[float, float] | None = None,
        max_pool_connections: int = MAX_POOL_CONNECTIONS,
        socket_options: list[Any] | None = None,
        client_cert: str | tuple[str, str] | None = None,
        proxies_config: dict[str, str] | None = None,
        connector_args: dict[str, Any] | None = None,
    ):
        if httpx is None:  # pragma: no cover
            raise RuntimeError(
                "Using HttpxSession requires httpx to be installed"
            )

        self._proxy_config = ProxyConfiguration(
            proxies=proxies, proxies_settings=proxies_config
        )

        if connector_args is None:
            self._connector_args: dict[str, Any] = {
                'keepalive_timeout': DEFAULT_KEEPALIVE_TIMEOUT
            }
        else:
            self._connector_args = connector_args

        # TODO: neither this nor AIOHTTPSession handles socket_options
        self._session: httpx.AsyncClient | None = None
        conn_timeout: float | None
        read_timeout: float | None

        if isinstance(timeout, (list, tuple)):
            conn_timeout, read_timeout = timeout
        else:
            conn_timeout = read_timeout = timeout

        write_timeout = self._connector_args.get('write_timeout', 5)
        pool_timeout = self._connector_args.get('pool_timeout', 5)

        self._timeout = httpx.Timeout(
            connect=conn_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )

        self._cert_file = None
        self._key_file = None
        if isinstance(client_cert, str):
            self._cert_file = client_cert
        elif isinstance(client_cert, tuple):
            self._cert_file, self._key_file = client_cert
        elif client_cert is not None:
            raise TypeError(f'{client_cert} must be str or tuple[str,str]')

        if 'use_dns_cache' in self._connector_args:
            raise NotImplementedError(
                "DNS caching is not implemented by httpx. https://github.com/encode/httpx/discussions/2211"
            )
        if 'force_close' in self._connector_args:
            raise NotImplementedError("Not supported with httpx as backend.")
        if 'resolver' in self._connector_args:
            raise NotImplementedError("Not supported with httpx as backend.")

        self._max_pool_connections = max_pool_connections
        self._socket_options = socket_options
        if socket_options is None:
            self._socket_options = []

        # SSL context construction (load_cert_chain / load_verify_locations /
        # create_urllib3_context's default cert load) does blocking file I/O.
        # Defer it to __aenter__ so it runs off the event loop. (#1469)
        self._verify: bool | str | SSLContext = verify
        if verify and 'ssl_context' in self._connector_args:
            self._verify = cast(
                'SSLContext', self._connector_args['ssl_context']
            )

    def _build_verify_context(self) -> SSLContext:
        # The endpoint's verify settings, without the client certificate.
        ssl_context = self._get_ssl_context()
        if self._verify:
            # urllib3 disables this by default because it verifies the hostname
            # itself; httpcore leaves it to the context.
            ssl_context.check_hostname = True
            ca_certs = get_cert_path(self._verify)
            if ca_certs:
                ssl_context.load_verify_locations(ca_certs, None, None)
        else:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    def _build_ssl_context(self) -> SSLContext:
        # Synchronous SSL context construction. Caller runs off the event loop.
        ssl_context = self._build_verify_context()
        if self._cert_file:
            # urllib3 keeps sending the client certificate when cert_reqs is
            # CERT_NONE, so this is not conditional on verify.
            ssl_context.load_cert_chain(self._cert_file, self._key_file)
        return ssl_context

    def _setup_proxy_ssl_context(self, proxy_url: str) -> SSLContext:
        proxies_settings = self._proxy_config.settings
        proxy_ca_bundle = proxies_settings.get('proxy_ca_bundle')
        proxy_cert = proxies_settings.get('proxy_client_cert')

        # The proxy connection gets the endpoint's verify settings but never
        # its client certificate: urllib3 passes cert_file=None when wrapping
        # the proxy socket, so only proxy_client_cert is offered to a proxy.
        context = self._build_verify_context()
        try:
            url = parse_url(proxy_url)
            # urllib3 disables this by default but we need it for proper
            # proxy tls negotiation when proxy_url is not an IP Address
            if self._verify and not _is_ipaddress(url.host):
                context.check_hostname = True
            if proxy_ca_bundle is not None:
                context.load_verify_locations(cafile=proxy_ca_bundle)

            if isinstance(proxy_cert, tuple):
                context.load_cert_chain(proxy_cert[0], keyfile=proxy_cert[1])
            elif isinstance(proxy_cert, str):
                context.load_cert_chain(proxy_cert)

            return context
        except (OSError, LocationParseError) as e:
            raise InvalidProxiesConfigError(error=e)

    def _build_ssl_contexts(
        self, proxy_urls: dict[str, str]
    ) -> tuple[SSLContext, dict[str, SSLContext]]:
        # Synchronous SSL context construction. Caller runs off the event loop.
        verify = (
            self._verify
            if isinstance(self._verify, ssl.SSLContext)
            else self._build_ssl_context()
        )
        # Only an https proxy does a TLS handshake of its own; httpx rejects
        # ssl_context on an http proxy.
        proxy_ssl_contexts = {
            proxy_url: self._setup_proxy_ssl_context(proxy_url)
            for proxy_url in set(proxy_urls.values())
            if urlparse(proxy_url).scheme == 'https'
        }
        return verify, proxy_ssl_contexts

    async def __aenter__(self):
        assert not self._session

        # Resolve the proxy URL for each scheme so we can mount a transport
        # per proxy. httpx configures proxies on the client/transport rather
        # than per-request the way aiohttp does. {scheme: fixed proxy url}
        self._proxy_urls = {
            scheme: proxy_url
            for scheme in ('http', 'https')
            if (proxy_url := self._proxy_config.proxy_url_for(f'{scheme}://'))
        }

        # Build the SSL contexts off the event loop on first entry — blocking
        # file I/O for the endpoint verify context and any proxy TLS. (#1469)
        self._proxy_ssl_contexts: dict[str, SSLContext] = {}
        (
            self._verify,
            self._proxy_ssl_contexts,
        ) = await anyio.to_thread.run_sync(
            self._build_ssl_contexts, self._proxy_urls
        )

        self._limits = httpx.Limits(
            max_connections=self._max_pool_connections,
            keepalive_expiry=self._connector_args['keepalive_timeout'],
        )

        # The AsyncClient is built lazily on the first send: httpx bakes proxy
        # headers into the client, and BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER
        # needs the request host, which we only learn then. A given session
        # only ever talks to one endpoint host, so it's built exactly once.
        return self

    def _make_async_client(
        self, proxy_host_header: str | None
    ) -> httpx.AsyncClient:
        mounts = {}
        for scheme, proxy_url in self._proxy_urls.items():
            headers = self._proxy_config.proxy_headers_for(proxy_url)
            if proxy_host_header is not None:
                # Experimental: mirror botocore's conn.proxy_headers['host'].
                headers = {**headers, 'host': proxy_host_header}
            proxy = httpx.Proxy(
                url=proxy_url,
                headers=headers or None,
                ssl_context=self._proxy_ssl_contexts.get(proxy_url),
            )
            mounts[f'{scheme}://'] = httpx.AsyncHTTPTransport(
                verify=self._verify,
                limits=self._limits,
                proxy=proxy,
            )

        # verify carries the endpoint TLS settings, including the client
        # certificate; the proxy hop above gets a context without it. Requests
        # matching a mounted proxy transport use that transport instead.
        return httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
            mounts=mounts,
            verify=self._verify,
        )

    def _get_session(self, request_url: str) -> httpx.AsyncClient:
        # Build the client on first use (see __aenter__). No await between the
        # None check and the assignment, so this is race-free under
        # cooperative scheduling even with concurrent sends.
        if self._session is None:
            proxy_host_header = None
            if self._proxy_urls and ensure_boolean(
                os.environ.get('BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER', '')
            ):
                # This is currently an "experimental" feature which provides
                # no guarantees of backwards compatibility. It may be subject
                # to change or removal in any patch version. Anyone opting in
                # to this feature should strictly pin botocore.
                proxy_host_header = urlparse(request_url).hostname
            self._session = self._make_async_client(proxy_host_header)
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
            self._session = None

    def _get_ssl_context(self) -> SSLContext:
        return create_urllib3_context()

    async def close(self) -> None:
        await self.__aexit__(None, None, None)

    async def send(
        self, request: AWSPreparedRequest
    ) -> aiobotocore.awsrequest.HttpxAWSResponse:
        # A proxy is mounted per scheme; when one matches this request, any
        # connection failure is a failure to reach the proxy rather than the
        # endpoint. httpx surfaces a forward (http) proxy failure as a plain
        # ConnectError, so we key off the configured proxy instead. None when
        # no proxy applies.
        proxy_url = self._proxy_config.proxy_url_for(request.url)
        try:
            url = request.url
            headers = request.headers

            headers_ = CIMultiDict(
                (z[0], _text(z[1], encoding='utf-8')) for z in headers.items()
            )

            # https://github.com/boto/botocore/issues/1255
            headers_['Accept-Encoding'] = 'identity'

            # content can also be https://github.com/ymyzk/tox-gh-actions
            content: AsyncIterable | bytes | bytearray | str | None = None

            async def to_async_iterable(stream: Iterable) -> AsyncIterable:
                if isinstance(stream, AsyncIterable):
                    async for item in stream:
                        yield item
                else:
                    for item in stream:
                        yield item
                        await anyio.sleep(0)  # Yield control to event loop

            if isinstance(
                request.body, (AsyncIterable, io.BytesIO)
            ) and isinstance(request.body, Iterable):
                content = to_async_iterable(request.body)
            else:
                content = request.body

            # The target gets used as the HTTP target instead of the URL path
            # it does not get normalized or otherwise processed, which is important
            # since arbitrary dots and slashes are valid as key paths.
            # See test_basic_s3.test_non_normalized_key_paths
            # This way of using it is currently ~undocumented, but recommended in
            # https://github.com/encode/httpx/discussions/1805#discussioncomment-8975989
            #
            # Skip it when proxying: httpcore reuses the request's extensions for
            # the proxy CONNECT request, and its "target" handling would replace
            # the CONNECT authority (host:port) with this full URL, producing an
            # invalid `CONNECT https://host/path` line. httpx/httpcore already
            # build the correct target for both forward and tunnelled proxies.
            extensions = (
                {} if proxy_url else {"target": bytes(url, encoding='utf-8')}
            )

            session = self._get_session(url)

            httpx_request = session.build_request(
                method=request.method,
                url=url,
                headers=headers,
                content=content,
                extensions=extensions,
            )
            assert isinstance(httpx_request.stream, httpx.AsyncByteStream)
            # auth, follow_redirects
            response = await session.send(httpx_request, stream=True)
            response_headers = botocore.compat.HTTPHeaders.from_pairs(
                response.headers.items()
            )

            http_response = aiobotocore.awsrequest.HttpxAWSResponse(
                str(response.url),
                response.status_code,
                response_headers,
                response,
            )

            if not request.stream_output:
                # Cause the raw stream to be exhausted immediately. We do it
                # this way instead of using preload_content because
                # preload_content will never buffer chunked responses
                await http_response.content

            return http_response

        except httpx.ConnectError as e:
            # httpx wraps a failed TLS handshake in a ConnectError; botocore
            # (and the aiohttp backend) report those as an SSLError.
            if (ssl_error := _find_ssl_error(e)) is not None:
                raise botocore.exceptions.SSLError(
                    endpoint_url=request.url, error=ssl_error
                )
            # A forward (http) proxy that can't be reached surfaces here rather
            # than as a ProxyError, so attribute it to the proxy when one applies.
            if proxy_url:
                raise ProxyConnectionError(
                    proxy_url=mask_proxy_url(proxy_url), error=e
                )
            raise EndpointConnectionError(endpoint_url=request.url, error=e)
        except (socket.gaierror,) as e:
            raise EndpointConnectionError(endpoint_url=request.url, error=e)
        except asyncio.TimeoutError as e:
            raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except httpx.ReadTimeout as e:
            raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except httpx.TimeoutException as e:
            raise ConnectTimeoutError(endpoint_url=request.url, error=e)
        except httpx.ProxyError as e:
            raise ProxyConnectionError(
                proxy_url=mask_proxy_url(proxy_url), error=e
            )
        except httpx.CloseError as e:
            raise ConnectionClosedError(endpoint_url=request.url, error=e)
        except ssl.SSLError:
            raise botocore.exceptions.SSLError

        except NotImplementedError:
            raise  # Avoid turning it into HTTPClientError.
        except CancelledError:
            raise
        except Exception as e:
            message = 'Exception received when sending httpx HTTP request'
            logger.debug(message, exc_info=True)
            raise HTTPClientError(error=e)


def is_httpx_session_cls(http_session_cls) -> bool:
    """Whether ``http_session_cls`` selects the httpx backend.

    aiohttp is asyncio-only; the httpx backend also runs on trio. Not a bare
    ``issubclass``: this reaches us straight from user config, and need not
    be a class at all.
    """
    return isinstance(http_session_cls, type) and issubclass(
        http_session_cls, HttpxSession
    )
