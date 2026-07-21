import asyncio
import contextlib
import io
import os
import socket
import ssl
from concurrent.futures import CancelledError

import aiohttp  # lgtm [py/import-and-import-from]
from aiohttp import (
    ClientConnectionError,
    ClientConnectorError,
    ClientHttpProxyError,
    ClientProxyConnectionError,
    ClientSSLError,
    ServerDisconnectedError,
    ServerTimeoutError,
)
from aiohttp.client import URL
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
    SSLError,
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

from ._constants import DEFAULT_KEEPALIVE_TIMEOUT
from ._endpoint_helpers import _IOBaseWrapper, _text


class _ProxySSLTCPConnector(aiohttp.TCPConnector):
    """A TCPConnector that uses a separate SSL context for the proxy hop.

    aiohttp builds the proxy request with ``ssl=req.ssl``, so the proxy
    connection and the tunnelled endpoint connection would otherwise share one
    context — and the endpoint's client certificate would be offered to the
    proxy. urllib3 passes ``cert_file=None`` when wrapping the proxy socket, so
    botocore never does that; this keeps the two apart the same way.
    """

    def __init__(self, *args, proxy_ssl_context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._proxy_ssl_context = proxy_ssl_context

    def _update_proxy_auth_header_and_build_proxy_req(self, req):
        proxy_req = super()._update_proxy_auth_header_and_build_proxy_req(req)
        if self._proxy_ssl_context is not None:
            proxy_req._ssl = self._proxy_ssl_context
        return proxy_req


class AIOHTTPSession:
    def __init__(
        self,
        verify: bool = True,
        proxies: dict[str, str] = None,  # {scheme: url}
        timeout: float = None,
        max_pool_connections: int = MAX_POOL_CONNECTIONS,
        socket_options=None,
        client_cert=None,
        proxies_config=None,
        connector_args=None,
    ):
        self._exit_stack = contextlib.AsyncExitStack()

        # TODO: handle socket_options
        # keep track of sessions by proxy url (if any)
        self._sessions: dict[str | None, aiohttp.ClientSession] | None = None
        self._verify = verify
        self._proxy_config = ProxyConfiguration(
            proxies=proxies, proxies_settings=proxies_config
        )
        if isinstance(timeout, (list, tuple)):
            conn_timeout, read_timeout = timeout
        else:
            conn_timeout = read_timeout = timeout

        timeout = aiohttp.ClientTimeout(
            sock_connect=conn_timeout, sock_read=read_timeout
        )

        self._cert_file = None
        self._key_file = None
        if isinstance(client_cert, str):
            self._cert_file = client_cert
        elif isinstance(client_cert, tuple):
            self._cert_file, self._key_file = client_cert

        self._timeout = timeout
        self._connector_args = connector_args
        if self._connector_args is None:
            self._connector_args = dict(
                keepalive_timeout=DEFAULT_KEEPALIVE_TIMEOUT
            )

        self._max_pool_connections = max_pool_connections
        self._socket_options = socket_options
        if socket_options is None:
            self._socket_options = []

        # aiohttp handles 100 continue so we shouldn't need AWSHTTP[S]ConnectionPool
        # it also pools by host so we don't need a manager, and can pass proxy via
        # request so don't need proxy manager

    async def __aenter__(self):
        assert self._sessions is None
        self._sessions = {}

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        assert self._sessions is not None, 'Session was never entered'
        self._sessions.clear()
        await self._exit_stack.aclose()
        # Make _sessions unusable once context is exited
        self._sessions = None

    def _get_ssl_context(self):
        return create_urllib3_context()

    def _setup_proxy_ssl_context(self, proxy_url):
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

    def _chunked(self, headers):
        transfer_encoding = headers.get('Transfer-Encoding', '')
        if chunked := transfer_encoding.lower() == 'chunked':
            # aiohttp wants chunking as a param, and not a header
            del headers['Transfer-Encoding']
        return chunked or None

    def _build_verify_context(self):
        # The endpoint's verify settings, without the client certificate.
        ssl_context = self._get_ssl_context()
        if self._verify:
            # urllib3 disables this by default because it verifies the hostname
            # itself; aiohttp leaves it to the context.
            ssl_context.check_hostname = True
            # inline self._setup_ssl_cert
            ca_certs = get_cert_path(self._verify)
            if ca_certs:
                ssl_context.load_verify_locations(ca_certs, None, None)
        else:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    def _build_ssl_contexts(self, proxy_url):
        # Synchronous SSL context construction. Caller runs off the event loop.
        # (#1469)
        ssl_context = self._build_verify_context()
        if self._cert_file:
            # urllib3 keeps sending the client certificate when cert_reqs is
            # CERT_NONE, so this is not conditional on verify.
            ssl_context.load_cert_chain(self._cert_file, self._key_file)

        # TODO: add support for
        #    proxies_settings.get('proxy_use_forwarding_for_https')
        proxy_ssl_context = (
            self._setup_proxy_ssl_context(proxy_url) if proxy_url else None
        )
        return ssl_context, proxy_ssl_context

    async def _create_connector(self, proxy_url):
        # TCPConnector binds the running loop, so build it here.
        # Dispatch blocking SSL file I/O to a thread. (#1469)
        ssl_context, proxy_ssl_context = await asyncio.to_thread(
            self._build_ssl_contexts, proxy_url
        )
        return _ProxySSLTCPConnector(
            limit=self._max_pool_connections,
            ssl=ssl_context,
            proxy_ssl_context=proxy_ssl_context,
            **self._connector_args,
        )

    async def _get_session(self, proxy_url):
        if not (session := self._sessions.get(proxy_url)):
            connector = await self._create_connector(proxy_url)
            self._sessions[proxy_url] = (
                session
            ) = await self._exit_stack.enter_async_context(
                aiohttp.ClientSession(
                    connector=connector,
                    timeout=self._timeout,
                    skip_auto_headers={'CONTENT-TYPE'},
                    auto_decompress=False,
                ),
            )

        return session

    async def close(self):
        await self.__aexit__(None, None, None)

    async def send(self, request):
        try:
            proxy_url = self._proxy_config.proxy_url_for(request.url)
            proxy_headers = self._proxy_config.proxy_headers_for(request.url)
            url = request.url
            headers = request.headers
            data = request.body

            if ensure_boolean(
                os.environ.get('BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER', '')
            ):
                # This is currently an "experimental" feature which provides
                # no guarantees of backwards compatibility. It may be subject
                # to change or removal in any patch version. Anyone opting in
                # to this feature should strictly pin botocore.
                host = urlparse(request.url).hostname
                proxy_headers['host'] = host

            headers_ = CIMultiDict(
                (z[0], _text(z[1], encoding='utf-8')) for z in headers.items()
            )

            # https://github.com/boto/botocore/issues/1255
            headers_['Accept-Encoding'] = 'identity'

            if isinstance(data, io.IOBase):
                data = _IOBaseWrapper(data)

            url = URL(url, encoded=True)
            session = await self._get_session(proxy_url)
            response = await session.request(
                request.method,
                url=url,
                chunked=self._chunked(headers_),
                headers=headers_,
                data=data,
                proxy=proxy_url,
                proxy_headers=proxy_headers,
            )

            # botocore converts keys to str, so make sure that they are in
            # the expected case. See detailed discussion here:
            # https://github.com/aio-libs/aiobotocore/pull/116
            # aiohttp's CIMultiDict camel cases the headers :(
            headers = {
                k.decode('utf-8').lower(): v.decode('utf-8')
                for k, v in response.raw_headers
            }

            http_response = aiobotocore.awsrequest.AioAWSResponse(
                str(response.url), response.status, headers, response
            )

            if not request.stream_output:
                # Cause the raw stream to be exhausted immediately. We do it
                # this way instead of using preload_content because
                # preload_content will never buffer chunked responses
                await http_response.content

            return http_response
        except ClientSSLError as e:
            raise SSLError(endpoint_url=request.url, error=e)
        except (ClientProxyConnectionError, ClientHttpProxyError) as e:
            raise ProxyConnectionError(
                proxy_url=mask_proxy_url(proxy_url), error=e
            )
        except (
            ServerDisconnectedError,
            aiohttp.ClientPayloadError,
            aiohttp.http_exceptions.BadStatusLine,
        ) as e:
            raise ConnectionClosedError(
                error=e, request=request, endpoint_url=request.url
            )
        except ServerTimeoutError as e:
            if str(e).lower().startswith('connect'):
                raise ConnectTimeoutError(endpoint_url=request.url, error=e)
            else:
                raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except (
            ClientConnectorError,
            ClientConnectionError,
            socket.gaierror,
        ) as e:
            raise EndpointConnectionError(endpoint_url=request.url, error=e)
        except asyncio.TimeoutError as e:
            raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except CancelledError:
            raise
        except Exception as e:
            message = 'Exception received when sending urllib3 HTTP request'
            logger.debug(message, exc_info=True)
            raise HTTPClientError(error=e)
