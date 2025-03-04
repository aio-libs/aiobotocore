from __future__ import annotations

import asyncio
import io
import os
import socket
from typing import TYPE_CHECKING, Any, cast

import botocore
from botocore.awsrequest import AWSPreparedRequest
from botocore.httpsession import (
    MAX_POOL_CONNECTIONS,
    EndpointConnectionError,
    HTTPClientError,
    ReadTimeoutError,
    create_urllib3_context,
    ensure_boolean,
    get_cert_path,
    logger,
)
from multidict import CIMultiDict

import aiobotocore.awsrequest
from aiobotocore._endpoint_helpers import _text

# TODO: resolve future annotations thing


try:
    import httpx
except ImportError:
    httpx = None

if TYPE_CHECKING:
    from ssl import SSLContext


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
        if proxies or proxies_config:
            raise NotImplementedError(
                "Proxy support not implemented with httpx as backend."
            )

        # TODO: handle socket_options
        self._session: httpx.AsyncClient | None = None
        conn_timeout: float | None
        read_timeout: float | None

        if isinstance(timeout, (list, tuple)):
            conn_timeout, read_timeout = timeout
        else:
            conn_timeout = read_timeout = timeout
        # must specify a default or set all four parameters explicitly
        # 5 is httpx default default
        self._timeout = httpx.Timeout(
            5, connect=conn_timeout, read=read_timeout
        )

        self._cert_file = None
        self._key_file = None
        if isinstance(client_cert, str):
            self._cert_file = client_cert
        elif isinstance(client_cert, tuple):
            self._cert_file, self._key_file = client_cert
        elif client_cert is not None:
            raise TypeError(f'{client_cert} must be str or tuple[str,str]')

        # previous logic was: if no connector args, specify keepalive_expiry=12
        # if any connector args, don't specify keepalive_expiry.
        # That seems .. weird to me? I'd expect "specify keepalive_expiry if user doesn't"
        # but keeping logic the same for now.
        if connector_args is None:
            # aiohttp default was 30
            # AWS has a 20 second idle timeout:
            #   https://web.archive.org/web/20150926192339/https://forums.aws.amazon.com/message.jspa?messageID=215367
            # "httpx default timeout is 5s so set something reasonable here"
            self._connector_args: dict[str, Any] = {'keepalive_timeout': 12}
        else:
            self._connector_args = connector_args

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

        # TODO [httpx]: clean up
        ssl_context: SSLContext | None = None
        self._verify: bool | str | SSLContext = verify
        if not verify:
            return
        if 'ssl_context' in self._connector_args:
            self._verify = cast(
                'SSLContext', self._connector_args['ssl_context']
            )
            return

        ssl_context = self._get_ssl_context()

        # inline self._setup_ssl_cert
        ca_certs = get_cert_path(verify)
        if ca_certs:
            ssl_context.load_verify_locations(ca_certs, None, None)
        if ssl_context is not None:
            self._verify = ssl_context

    async def __aenter__(self):
        assert not self._session

        limits = httpx.Limits(
            max_connections=self._max_pool_connections,
            # 5 is httpx default, specifying None is no limit
            keepalive_expiry=self._connector_args.get('keepalive_timeout', 5),
        )

        # TODO [httpx]: I put logic here to minimize diff / accidental downstream
        # consequences - but can probably put this logic in __init__
        if self._cert_file and self._key_file is None:
            cert = self._cert_file
        elif self._cert_file:
            cert = (self._cert_file, self._key_file)
        else:
            cert = None

        # TODO [httpx]: skip_auto_headers={'Content-TYPE'} ?
        # TODO [httpx]: auto_decompress=False ?

        self._session = httpx.AsyncClient(
            timeout=self._timeout, limits=limits, cert=cert
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
            self._session = None
            self._connector = None

    def _get_ssl_context(self) -> SSLContext:
        ssl_context = create_urllib3_context()
        if self._cert_file:
            ssl_context.load_cert_chain(self._cert_file, self._key_file)
        return ssl_context

    async def close(self) -> None:
        await self.__aexit__(None, None, None)

    async def send(
        self, request: AWSPreparedRequest
    ) -> aiobotocore.awsrequest.AioAWSResponse:
        try:
            url = request.url
            headers = request.headers
            data: io.IOBase | str | bytes | bytearray | None = request.body

            # currently no support for BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER
            if ensure_boolean(
                os.environ.get('BOTO_EXPERIMENTAL__ADD_PROXY_HOST_HEADER', '')
            ):
                raise NotImplementedError(
                    'httpx implementation of aiobotocore does not (currently) support proxies'
                )

            headers_ = CIMultiDict(
                (z[0], _text(z[1], encoding='utf-8')) for z in headers.items()
            )

            # https://github.com/boto/botocore/issues/1255
            headers_['Accept-Encoding'] = 'identity'

            content: bytes | bytearray | str | None = None
            # TODO: test that sends a bytearray

            if isinstance(data, io.IOBase):
                # TODO [httpx]: httpx really wants an async iterable that is not also a
                # sync iterable (??). Seems like there should be an easy answer, but I
                # just convert it to bytes for now.
                k = data.readlines()
                if len(k) == 0:
                    content = b''  # TODO: uncovered
                elif len(k) == 1:
                    content = k[0]
                else:
                    assert False  # TODO: uncovered
            else:
                content = data

            assert self._session

            # The target gets used as the HTTP target instead of the URL path
            # it does not get normalized or otherwise processed, which is important
            # since arbitrary dots and slashes are valid as key paths.
            # See test_basic_s3.test_non_normalized_key_paths
            # This way of using it is currently ~undocumented, but recommended in
            # https://github.com/encode/httpx/discussions/1805#discussioncomment-8975989
            extensions = {"target": bytes(url, encoding='utf-8')}

            httpx_request = self._session.build_request(
                method=request.method,
                url=url,
                headers=headers,
                content=content,
                extensions=extensions,
            )
            # auth, follow_redirects
            response = await self._session.send(httpx_request, stream=True)
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

        # **previous exception mapping**
        # aiohttp.ClientSSLError -> SSLError

        # aiohttp.ClientProxyConnectiorError
        # aiohttp.ClientHttpProxyError -> ProxyConnectionError

        # aiohttp.ServerDisconnectedError
        # aiohttp.ClientPayloadError
        # aiohttp.http_exceptions.BadStatusLine -> ConnectionClosedError

        # aiohttp.ServerTimeoutError -> ConnectTimeoutError|ReadTimeoutError

        # aiohttp.ClientConnectorError
        # aiohttp.ClientConnectionError
        # socket.gaierror -> EndpointConnectionError

        # asyncio.TimeoutError -> ReadTimeoutError

        # **possible httpx exception mapping**
        # httpx.CookieConflict
        # httpx.HTTPError
        # * httpx.HTTPStatusError
        # * httpx.RequestError
        #   * httpx.DecodingError
        #   * httpx.TooManyRedirects
        # * httpx.TransportError
        #   * httpx.NetworkError
        #     * httpx.CloseError -> ConnectionClosedError
        #     * httpx.ConnectError -> EndpointConnectionError
        #     * httpx.ReadError
        #     * httpx.WriteError
        #   * httpx.ProtocolError
        #     * httpx.LocalProtocolError -> SSLError??
        #     * httpx.RemoteProtocolError
        #   * httpx.ProxyError -> ProxyConnectionError
        #   * httpx.TimeoutException
        #     * httpx.ConnectTimeout -> ConnectTimeoutError
        #     * httpx.PoolTimeout
        #     * httpx.ReadTimeout -> ReadTimeoutError
        #     * httpx.WriteTimeout
        #   * httpx.UnsupportedProtocol
        # * httpx.InvalidURL

        except httpx.ConnectError as e:
            raise EndpointConnectionError(endpoint_url=request.url, error=e)
        except (socket.gaierror,) as e:
            raise EndpointConnectionError(endpoint_url=request.url, error=e)
        except asyncio.TimeoutError as e:
            raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except httpx.ReadTimeout as e:
            raise ReadTimeoutError(endpoint_url=request.url, error=e)
        except NotImplementedError:
            raise
        except Exception as e:
            message = 'Exception received when sending urllib3 HTTP request'
            logger.debug(message, exc_info=True)
            raise HTTPClientError(error=e)
