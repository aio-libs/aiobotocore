import aiohttp
import asyncio
import functools
import sys
import yarl
import io
import wrapt
import botocore.retryhandler
import aiohttp.http_exceptions
from aiohttp.client_proto import ResponseHandler
from aiohttp.helpers import CeilTimeout
from aiohttp.client_reqrep import ClientResponse
from botocore.endpoint import EndpointCreator, Endpoint, DEFAULT_TIMEOUT, \
    MAX_POOL_CONNECTIONS, logger
from botocore.exceptions import EndpointConnectionError, ConnectionClosedError
from botocore.hooks import first_non_none_response
from botocore.utils import is_valid_endpoint_url
from botocore.vendored.requests.structures import CaseInsensitiveDict
from packaging.version import parse as parse_version
from multidict import MultiDict
from urllib.parse import urlparse


PY_35 = sys.version_info >= (3, 5)
AIOHTTP_2 = parse_version(aiohttp.__version__) > parse_version('2.0.0')

# Monkey patching: We need to insert the aiohttp exception equivalents
# The only other way to do this would be to have another config file :(
_aiohttp_retryable_exceptions = [
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    aiohttp.http_exceptions.HttpProcessingError,
    asyncio.TimeoutError,
]

botocore.retryhandler.EXCEPTION_MAP['GENERAL_CONNECTION_ERROR'].extend(
    _aiohttp_retryable_exceptions
)


def text_(s, encoding='utf-8', errors='strict'):
    if isinstance(s, bytes):
        return s.decode(encoding, errors)
    return s  # pragma: no cover


# Unfortunately aiohttp changed the behavior of streams:
#   github.com/aio-libs/aiohttp/issues/1907
# We need this wrapper until we have a final resolution
if AIOHTTP_2:
    class _IOBaseWrapper(wrapt.ObjectProxy):
        def close(self):
            # this stream should not be closed by aiohttp, like 1.x
            pass


@asyncio.coroutine
def convert_to_response_dict(http_response, operation_model):
    response_dict = {
        # botocore converts keys to str, so make sure that they are in
        # the expected case. See detailed discussion here:
        # https://github.com/aio-libs/aiobotocore/pull/116
        # aiohttp's CIMultiDict camel cases the headers :(
        'headers': CaseInsensitiveDict(
            {k.decode('utf-8').lower(): v.decode('utf-8')
             for k, v in http_response.raw_headers}),
        'status_code': http_response.status_code,
    }

    if response_dict['status_code'] >= 300:
        body = yield from http_response.read()
        response_dict['body'] = body
    elif operation_model.has_streaming_output:
        response_dict['body'] = http_response.raw
    else:
        body = yield from http_response.read()
        response_dict['body'] = body
    return response_dict


# This is similar to botocore.response.StreamingBody
class ClientResponseContentProxy(wrapt.ObjectProxy):
    """Proxy object for content stream of http response.  This is here in case
    you want to pass around the "Body" of the response without closing the
    response itself."""

    def __init__(self, response):
        super().__init__(response.__wrapped__.content)
        self.__response = response

    def set_socket_timeout(self, timeout):
        """Set the timeout seconds on the socket."""
        # TODO: see if we can do this w/o grabbing _protocol
        self.__response._protocol.set_timeout(timeout)

    # Note: we don't have a __del__ method as the ClientResponse has a __del__
    # which will warn the user if they didn't close/release the response
    # explicitly.  A release here would mean reading all the unread data
    # (which could be very large), and a close would mean being unable to re-
    # use the connection, so the user MUST chose.  Default is to warn + close
    if PY_35:
        @asyncio.coroutine
        def __aenter__(self):
            yield from self.__response.__aenter__()
            return self

        @asyncio.coroutine
        def __aexit__(self, exc_type, exc_val, exc_tb):
            return (yield from self.__response.__aexit__(exc_type,
                                                         exc_val, exc_tb))

    def close(self):
        self.__response.close()


class ClientResponseProxy(wrapt.ObjectProxy):
    """Proxy object for http response useful for porting from
    botocore underlying http library."""

    def __init__(self, *args, **kwargs):
        super().__init__(ClientResponse(*args, **kwargs))

    @property
    def status_code(self):
        return self.status

    @property
    def content(self):
        # ClientResponse._content is set by the coroutine ClientResponse.read
        return self._content

    @property
    def raw(self):
        return ClientResponseContentProxy(self)


class WrappedResponseHandler(ResponseHandler):
    def __init__(self, *args, **kwargs):
        self.__wrapped_read_timeout = kwargs.pop('wrapped_read_timeout')
        super().__init__(*args, **kwargs)

    def set_timeout(self, timeout):
        self.__wrapped_read_timeout = timeout

    @asyncio.coroutine
    def _wrapped_wait(self, wrapped, instance, args, kwargs):
        with CeilTimeout(self.__wrapped_read_timeout, loop=self._loop):
            result = yield from wrapped(*args, **kwargs)
            return result

    @asyncio.coroutine
    def read(self):
        with CeilTimeout(self.__wrapped_read_timeout, loop=self._loop):
            resp_msg, stream_reader = yield from super().read()

            if hasattr(stream_reader, '_wait'):
                stream_reader._wait = wrapt.FunctionWrapper(
                    stream_reader._wait, self._wrapped_wait)

            return resp_msg, stream_reader


class AioEndpoint(Endpoint):
    def __init__(self, host,
                 endpoint_prefix, event_emitter, proxies=None, verify=True,
                 timeout=DEFAULT_TIMEOUT, response_parser_factory=None,
                 max_pool_connections=MAX_POOL_CONNECTIONS,
                 loop=None, connector_args=None):

        super().__init__(host, endpoint_prefix,
                         event_emitter, proxies=proxies, verify=verify,
                         timeout=timeout,
                         response_parser_factory=response_parser_factory,
                         max_pool_connections=max_pool_connections)

        if isinstance(timeout, (list, tuple)):
            self._conn_timeout, self._read_timeout = timeout
        else:
            self._conn_timeout = self._read_timeout = timeout

        self._loop = loop or asyncio.get_event_loop()

        if connector_args is None:
            # AWS has a 20 second idle timeout:
            #   https://forums.aws.amazon.com/message.jspa?messageID=215367
            # aiohttp default timeout is 30s so set something reasonable here
            connector_args = dict(keepalive_timeout=12)

        connector = aiohttp.TCPConnector(loop=self._loop,
                                         limit=max_pool_connections,
                                         verify_ssl=self.verify,
                                         **connector_args)

        # This begins the journey into our replacement of aiohttp's
        # `read_timeout`.  Their implementation represents an absolute time
        # from the initial request, to the last read.  So if the client delays
        # reading the body for long enough the request would be cancelled.
        # See https://github.com/aio-libs/aiobotocore/issues/245
        assert connector._factory.func == ResponseHandler

        connector._factory = functools.partial(
            WrappedResponseHandler,
            wrapped_read_timeout=self._read_timeout,
            *connector._factory.args,
            **connector._factory.keywords)

        self._aio_session = aiohttp.ClientSession(
            connector=connector,
            read_timeout=None,
            conn_timeout=self._conn_timeout,
            skip_auto_headers={'CONTENT-TYPE'},
            response_class=ClientResponseProxy,
            loop=self._loop)

    @asyncio.coroutine
    def _request(self, method, url, headers, data):
        # Note: When using aiobotocore with dynamodb, requests fail on crc32
        # checksum computation as soon as the response data reaches ~5KB.
        # When AWS response is gzip compressed:
        # 1. aiohttp is automatically decompressing the data
        # (http://aiohttp.readthedocs.io/en/stable/client.html#binary-response-content)
        # 2. botocore computes crc32 on the uncompressed data bytes and fails
        # cause crc32 has been computed on the compressed data
        # The following line forces aws not to use gzip compression,
        # if there is a way to configure aiohttp not to perform decompression,
        # we can remove the following line and take advantage of
        # aws gzip compression.
        # See: https://github.com/aio-libs/aiohttp/issues/1992
        headers['Accept-Encoding'] = 'identity'
        headers_ = MultiDict(
            (z[0], text_(z[1], encoding='utf-8')) for z in headers.items())

        # botocore does this during the request so we do this here as well
        proxy = self.proxies.get(urlparse(url.lower()).scheme)

        if AIOHTTP_2 and isinstance(data, io.IOBase):
            data = _IOBaseWrapper(data)

        url = yarl.URL(url, encoded=True)
        resp = yield from self._aio_session.request(method, url=url,
                                                    headers=headers_,
                                                    data=data,
                                                    proxy=proxy,
                                                    timeout=None,
                                                    allow_redirects=False)
        return resp

    @asyncio.coroutine
    def _send_request(self, request_dict, operation_model):
        attempts = 1
        request = self.create_request(request_dict, operation_model)
        success_response, exception = yield from self._get_response(
            request, operation_model, attempts)
        while (yield from self._needs_retry(attempts, operation_model,
                                            request_dict, success_response,
                                            exception)):
            attempts += 1
            # If there is a stream associated with the request, we need
            # to reset it before attempting to send the request again.
            # This will ensure that we resend the entire contents of the
            # body.
            request.reset_stream()
            # Create a new request when retried (including a new signature).
            request = self.create_request(
                request_dict, operation_model)
            success_response, exception = yield from self._get_response(
                request, operation_model, attempts)
        if success_response is not None and \
                'ResponseMetadata' in success_response[1]:
            # We want to share num retries, not num attempts.
            total_retries = attempts - 1
            success_response[1]['ResponseMetadata']['RetryAttempts'] = \
                total_retries
        if exception is not None:
            raise exception
        else:
            return success_response

    # NOTE: The only line changed here changing time.sleep to asyncio.sleep
    @asyncio.coroutine
    def _needs_retry(self, attempts, operation_model, request_dict,
                     response=None, caught_exception=None):
        event_name = 'needs-retry.%s.%s' % (self._endpoint_prefix,
                                            operation_model.name)
        responses = self._event_emitter.emit(
            event_name, response=response, endpoint=self,
            operation=operation_model, attempts=attempts,
            caught_exception=caught_exception, request_dict=request_dict)
        handler_response = first_non_none_response(responses)
        if handler_response is None:
            return False
        else:
            # Request needs to be retried, and we need to sleep
            # for the specified number of times.
            logger.debug("Response received to retry, sleeping for "
                         "%s seconds", handler_response)
            yield from asyncio.sleep(handler_response, loop=self._loop)
            return True

    @asyncio.coroutine
    def _get_response(self, request, operation_model, attempts):
        # This will return a tuple of (success_response, exception)
        # and success_response is itself a tuple of
        # (http_response, parsed_dict).
        # If an exception occurs then the success_response is None.
        # If no exception occurs then exception is None.
        try:
            # http request substituted too async one
            logger.debug("Sending http request: %s", request)

            resp = yield from self._request(
                request.method, request.url, request.headers, request.body)
            http_response = resp
        except aiohttp.ClientConnectionError as e:
            e.request = request  # botocore expects the request property

            # For a connection error, if it looks like it's a DNS
            # lookup issue, 99% of the time this is due to a misconfigured
            # region/endpoint so we'll raise a more specific error message
            # to help users.
            logger.debug("ConnectionError received when sending HTTP request.",
                         exc_info=True)

            if self._looks_like_dns_error(e):
                better_exception = EndpointConnectionError(
                    endpoint_url=request.url, error=e)
                return None, better_exception
            else:
                return None, e
        except aiohttp.http_exceptions.BadStatusLine:
            better_exception = ConnectionClosedError(
                endpoint_url=request.url, request=request)
            return None, better_exception
        except Exception as e:
            logger.debug("Exception received when sending HTTP request.",
                         exc_info=True)
            return None, e

        # This returns the http_response and the parsed_data.
        response_dict = yield from convert_to_response_dict(http_response,
                                                            operation_model)
        parser = self._response_parser_factory.create_parser(
            operation_model.metadata['protocol'])
        parsed_response = parser.parse(
            response_dict, operation_model.output_shape)
        return (http_response, parsed_response), None


class AioEndpointCreator(EndpointCreator):
    def __init__(self, event_emitter, loop):
        super().__init__(event_emitter)
        self._loop = loop

    def create_endpoint(self, service_model, region_name=None,
                        endpoint_url=None, verify=None,
                        response_parser_factory=None, timeout=DEFAULT_TIMEOUT,
                        max_pool_connections=MAX_POOL_CONNECTIONS,
                        connector_args=None):
        if not is_valid_endpoint_url(endpoint_url):
            raise ValueError("Invalid endpoint: %s" % endpoint_url)

        return AioEndpoint(
            endpoint_url,
            endpoint_prefix=service_model.endpoint_prefix,
            event_emitter=self._event_emitter,
            proxies=self._get_proxies(endpoint_url),
            verify=self._get_verify_value(verify),
            timeout=timeout,
            max_pool_connections=max_pool_connections,
            response_parser_factory=response_parser_factory,
            loop=self._loop, connector_args=connector_args)
