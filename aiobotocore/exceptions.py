import asyncio
import socket

from aiohttp import ClientSSLError, ClientConnectorError, ClientProxyConnectionError, \
    ClientHttpProxyError, ServerTimeoutError, ServerDisconnectedError

from botocore.exceptions import SSLError as boto_SSLError, \
    EndpointConnectionError as boto_EndpointConnectionError, \
    ProxyConnectionError as boto_ProxyConnectionError, \
    ConnectTimeoutError as boto_ConnectTimeoutError, \
    ReadTimeoutError as boto_ReadTimeoutError, \
    ConnectionClosedError as boto_ConnectionClosedError, \
    HTTPClientError as boto_HTTPClientError


# These map in the boto exception, which subclasses against both the base botocore
# exception and the underlying requests exception, to the equivalent exceptions which
# could be thrown via aiohttp
class SSLError(boto_SSLError, ClientSSLError):
    pass


class EndpointConnectionError(boto_EndpointConnectionError,
                              ClientConnectorError, socket.gaierror):
    pass


class ProxyConnectionError(boto_ProxyConnectionError, ClientProxyConnectionError,
                           ClientHttpProxyError):
    pass


class ConnectTimeoutError(boto_ConnectTimeoutError, ServerTimeoutError):
    pass


class ReadTimeoutError(boto_ReadTimeoutError, asyncio.TimeoutError):
    pass


class ConnectionClosedError(boto_ConnectionClosedError, ServerDisconnectedError):
    pass


class HTTPClientError(boto_HTTPClientError, Exception):
    pass
