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
    def __str__(self):  # to avoid picking up the str from aiohttp
        return boto_EndpointConnectionError.__str__(self)


class ProxyConnectionError(boto_ProxyConnectionError, ClientProxyConnectionError,
                           ClientHttpProxyError):
    def __str__(self):
        return boto_ProxyConnectionError.__str__(self)


class ConnectTimeoutError(boto_ConnectTimeoutError, ServerTimeoutError):
    def __str__(self):
        return boto_ConnectTimeoutError.__str__(self)

class ReadTimeoutError(boto_ReadTimeoutError, asyncio.TimeoutError):
    def __str__(self):
        return boto_ReadTimeoutError.__str__(self)

class ConnectionClosedError(boto_ConnectionClosedError, ServerDisconnectedError):
    def __str__(self):
        return boto_ConnectionClosedError.__str__(self)

class HTTPClientError(boto_HTTPClientError, Exception):
    def __str__(self):
        return boto_HTTPClientError.__str__(self)
