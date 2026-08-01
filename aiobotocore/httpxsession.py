from __future__ import annotations

import asyncio
import io
import socket
import ssl
import warnings
from collections.abc import AsyncIterable, Iterable
from concurrent.futures import CancelledError
from contextlib import AsyncExitStack
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
    get_cert_path,
    logger,
    mask_proxy_url,
    parse_url,
    urlparse,
)
from multidict import CIMultiDict

import aiobotocore.awsrequest
from aiobotocore._endpoint_helpers import _text
from aiobotocore._httpx import HTTPX_IS_LEGACY, httpx

from ._constants import DEFAULT_KEEPALIVE_TIMEOUT

if httpx is not None:
    # anyio is a dependency of both supported httpx implementations.
    import anyio.to_thread
if TYPE_CHECKING:
    from ssl import SSLContext

# Emit the legacy-httpx deprecation warning at most once per process. aiobotocore
# builds a fresh HttpxSession per client, and under a warning filter of 'always'
# an unguarded warning would fire on every instance.
_LEGACY_HTTPX_WARNED = False
_RAW_PROXY_TARGET = 'aiobotocore_raw_proxy_target'


class _ProxyTargetExtensions(dict):
    """Apply the raw target to the endpoint request, but not CONNECT.

    HTTPcore constructs the endpoint request first and then reuses its
    extensions when constructing CONNECT. Its ``Request`` checks for target
    once during construction, so exposing it only on the first check keeps the
    raw S3 path on the endpoint request without replacing CONNECT's authority.
    """

    def __init__(self, extensions: dict, target: bytes):
        super().__init__(extensions, target=target)
        self._target_applied = False

    def __contains__(self, key):
        if key == 'target':
            if self._target_applied:
                return False
            self._target_applied = True
        return super().__contains__(key)


if httpx is not None:

    class _ProxyTargetTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            target = request.extensions.pop(_RAW_PROXY_TARGET, None)
            if target is not None:
                # This is the last layer before HTTPcore constructs its
                # endpoint Request, followed by its CONNECT Request. Preserve
                # timeout and other HTTPX extensions while exposing target to
                # the first construction only.
                request.extensions = _ProxyTargetExtensions(
                    request.extensions, target
                )
            return await super().handle_async_request(request)


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
                "Using HttpxSession requires httpx2 (or httpx) to be installed"
            )
        global _LEGACY_HTTPX_WARNED
        if HTTPX_IS_LEGACY and not _LEGACY_HTTPX_WARNED:
            _LEGACY_HTTPX_WARNED = True
            warnings.warn(
                "aiobotocore's httpx backend now prefers httpx2, Pydantic's "
                "maintained fork of httpx. The legacy 'httpx' package is "
                "deprecated as a backend; install httpx2 (e.g. "
                "aiobotocore[httpx2]) to migrate.",
                DeprecationWarning,
                stacklevel=2,
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
        self._entered = False
        self._proxy_ssl_contexts: dict[str, SSLContext]
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

    def _clone_verify_context(self, source: SSLContext) -> SSLContext:
        """Copy endpoint verification settings without its client cert."""
        context = self._get_ssl_context()
        context.options = source.options
        context.minimum_version = source.minimum_version
        context.maximum_version = source.maximum_version
        context.verify_flags = source.verify_flags
        context.verify_mode = source.verify_mode
        context.check_hostname = source.check_hostname
        ca_certs = source.get_ca_certs(binary_form=True)
        if ca_certs:
            cadata = ''.join(
                ssl.DER_cert_to_PEM_cert(cert) for cert in ca_certs
            )
            context.load_verify_locations(cadata=cadata)
        return context

    def _setup_proxy_ssl_context(self, proxy_url: str) -> SSLContext:
        proxies_settings = self._proxy_config.settings
        proxy_ca_bundle = proxies_settings.get('proxy_ca_bundle')
        proxy_cert = proxies_settings.get('proxy_client_cert')

        # The proxy connection gets the endpoint's verify settings but never
        # its client certificate: urllib3 passes cert_file=None when wrapping
        # the proxy socket, so only proxy_client_cert is offered to a proxy.
        context = (
            self._clone_verify_context(self._verify)
            if isinstance(self._verify, ssl.SSLContext)
            else self._build_verify_context()
        )
        try:
            url = parse_url(proxy_url)
            # urllib3 disables this by default but we need it for proper
            # proxy tls negotiation when proxy_url is not an IP Address
            if context.verify_mode != ssl.CERT_NONE and not _is_ipaddress(
                url.host
            ):
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
        assert not self._entered
        self._entered = True
        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Resolve the proxy URL for each scheme so we can mount a transport
            # per proxy. httpx configures proxies on the client/transport rather
            # than per-request the way aiohttp does. {scheme: fixed proxy url}
            self._proxy_urls = {
                scheme: proxy_url
                for scheme in ('http', 'https')
                if (
                    proxy_url := self._proxy_config.proxy_url_for(
                        f'{scheme}://'
                    )
                )
            }

            # Build SSL contexts off the event loop on first entry — blocking
            # file I/O for endpoint verification and proxy TLS. (#1469)
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
            self._sessions: dict[None, httpx.AsyncClient] = {}
            self._session_lock = anyio.Lock()

            # The client is built lazily so constructing a session remains
            # side-effect free until it is entered and first used.
            return self
        except BaseException:
            self._entered = False
            raise

    def _make_async_client(self) -> httpx.AsyncClient:
        mounts = {}
        for scheme, proxy_url in self._proxy_urls.items():
            headers = self._proxy_config.proxy_headers_for(proxy_url)
            proxy = httpx.Proxy(
                url=proxy_url,
                headers=headers or None,
                ssl_context=self._proxy_ssl_contexts.get(proxy_url),
            )
            mounts[f'{scheme}://'] = _ProxyTargetTransport(
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
            # botocore resolves environment proxies itself (get_environ_proxies,
            # which also honours system bypass settings) and passes the result
            # in, so httpx must not apply its own — aiohttp and urllib3 only
            # ever see what botocore decided. trust_env also gates httpx's use
            # of SSL_CERT_FILE/SSL_CERT_DIR, which botocore ignores; that is
            # moot while verify is always a prebuilt SSLContext, but the
            # environment must not become a source of trust if that changes.
            trust_env=False,
        )

    async def _get_session(self, _request_url: str) -> httpx.AsyncClient:
        # httpcore generates CONNECT's Host header from each request target,
        # so one client can serve every target without multiplying pool limits.
        if None not in self._sessions:
            async with self._session_lock:
                if None not in self._sessions:
                    client = self._make_async_client()
                    self._sessions[None] = (
                        await self._exit_stack.enter_async_context(client)
                    )
        return self._sessions[None]

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            return await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._sessions = {}
            self._entered = False

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
            target = bytes(url, encoding='utf-8')
            extensions = {
                (_RAW_PROXY_TARGET if proxy_url else 'target'): target
            }

            session = await self._get_session(url)

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
