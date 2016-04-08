import asyncio
import sys

import aiohttp
import botocore.retryhandler
import botocore.endpoint

from aiohttp.client_reqrep import ClientResponse
from botocore.endpoint import EndpointCreator, Endpoint, DEFAULT_TIMEOUT
from botocore.exceptions import EndpointConnectionError, ConnectionClosedError
from botocore.utils import is_valid_endpoint_url
from botocore.hooks import first_non_none_response

PY_35 = sys.version_info >= (3, 5)

# Monkey patching: We need to insert the aiohttp exception equivalents
# The only other way to do this would be to have another config file :(
_aiohttp_retryable_exceptions = [
    aiohttp.errors.ClientConnectionError,
    aiohttp.errors.TimeoutError,
    aiohttp.errors.DisconnectedError,
    aiohttp.errors.ClientHttpProcessingError,
]

botocore.retryhandler.EXCEPTION_MAP['GENERAL_CONNECTION_ERROR'].extend(
    _aiohttp_retryable_exceptions
)


def text_(s, encoding='utf-8', errors='strict'):
    if isinstance(s, bytes):
        return s.decode(encoding, errors)
    return s  # pragma: no cover


@asyncio.coroutine
def convert_to_response_dict(http_response, operation_model):
    response_dict = {
        'headers': http_response.headers,
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


class ClientResponseContentProxy:
    """Proxy object for content stream of http response.  This is here in case
    you want to pass around the "Body" of the response without closing the
    response itself."""

    def __init__(self, response: ClientResponse):
        self.__response = response
        self.__content = self.__response.content

    def __getattr__(self, item):
        return getattr(self.__content, item)

    def __dir__(self):
        attrs = dir(self.__content)
        attrs.append('close')
        return attrs

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


class ClientResponseProxy:
    """Proxy object for http response useful for porting from
    botocore underlying http library."""

    # NOTE: unfortunately we cannot inherit from ClientReponse because it also
    # uses the `content` property
    def __init__(self, *args, **kwargs):
        self._response = ClientResponse(*args, **kwargs)

    @property
    def status_code(self):
        return self._response.status

    @property
    def content(self):
        # ClientResponse._content is set by the coroutine ClientResponse.read
        return self._response._content

    @property
    def raw(self):
        return ClientResponseContentProxy(self._response)

    def __getattr__(self, item):
        # All other properties forward to the underlying ClientResponse to
        # support things like aenter/aexit/read
        return getattr(self._response, item)


class AioEndpoint(Endpoint):

    def __init__(self, host,
                 endpoint_prefix, event_emitter, proxies=None, verify=True,
                 timeout=DEFAULT_TIMEOUT, response_parser_factory=None,
                 loop=None, connector_args=None):

        super().__init__(host, endpoint_prefix,
                         event_emitter, proxies=proxies, verify=verify,
                         timeout=timeout,
                         response_parser_factory=response_parser_factory)

        if isinstance(timeout, (list, tuple)):
            self._conn_timeout, self._read_timeout = timeout
        else:
            self._conn_timeout = self._read_timeout = timeout

        self._loop = loop or asyncio.get_event_loop()
        if connector_args is None:
            # AWS has a 20 second idle timeout:
            #   https://forums.aws.amazon.com/message.jspa?messageID=215367
            # aiohttp default timeout is 30s so set something reasonable here
            connector = aiohttp.TCPConnector(loop=self._loop,
                                             keepalive_timeout=12,
                                             conn_timeout=self._conn_timeout)
        else:
            connector = aiohttp.TCPConnector(loop=self._loop,
                                             conn_timeout=self._conn_timeout,
                                             **connector_args)

        self._aio_session = aiohttp.ClientSession(
            connector=connector,
            skip_auto_headers={'CONTENT-TYPE'},
            response_class=ClientResponseProxy, loop=self._loop)

    @asyncio.coroutine
    def _request(self, method, url, headers, data):
        headers_ = dict(
            (z[0], text_(z[1], encoding='utf-8')) for z in headers.items())
        request_coro = self._aio_session.request(method, url=url,
                                                 headers=headers_, data=data)
        resp = yield from asyncio.wait_for(
            request_coro, timeout=self._conn_timeout + self._read_timeout,
            loop=self._loop)
        return resp

    @asyncio.coroutine
    def _send_request(self, request_dict, operation_model):
        attempts = 1
        request = self.create_request(request_dict, operation_model)
        success_response, exception = yield from self._get_response(
            request, operation_model, attempts)
        while (yield from self._needs_retry(attempts, operation_model,
                                            success_response, exception)):
            attempts += 1
            # If there is a stream associated with the request, we need
            # to reset it before attempting to send the request again.
            # This will ensure that we resend the entire contents of the
            # body.
            request.reset_stream()
            # Create a new request when retried (including a new signature).
            request = self.create_request(
                request_dict, operation_model=operation_model)
            success_response, exception = yield from self._get_response(
                request, operation_model, attempts)
        if exception is not None:
            raise exception
        else:
            return success_response

    # NOTE: The only line changed here changing time.sleep to asyncio.sleep
    @asyncio.coroutine
    def _needs_retry(self, attempts, operation_model, response=None,
                     caught_exception=None):
        event_name = 'needs-retry.%s.%s' % (self._endpoint_prefix,
                                            operation_model.name)
        responses = self._event_emitter.emit(
            event_name, response=response, endpoint=self,
            operation=operation_model, attempts=attempts,
            caught_exception=caught_exception)
        handler_response = first_non_none_response(responses)
        if handler_response is None:
            return False
        else:
            # Request needs to be retried, and we need to sleep
            # for the specified number of times.
            botocore.retryhandler.logger.debug("Response received to retry, "
                                               "sleeping for %s seconds",
                                               handler_response)

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
            botocore.endpoint.logger.debug("Sending http request: %s", request)
            resp = yield from self._request(
                request.method, request.url, request.headers, request.body)
            http_response = resp
        except aiohttp.errors.BadStatusLine:
            better_exception = ConnectionClosedError(
                endpoint_url=request.url, request=request)
            return None, better_exception
        except aiohttp.errors.ClientConnectionError as e:
            e.request = request  # botocore expects the request property

            # For a connection error, if it looks like it's a DNS
            # lookup issue, 99% of the time this is due to a misconfigured
            # region/endpoint so we'll raise a more specific error message
            # to help users.
            botocore.endpoint.logger.debug("ConnectionError received when "
                                           "sending HTTP request.",
                                           exc_info=True)

            if self._looks_like_dns_error(e):
                better_exception = EndpointConnectionError(
                    endpoint_url=request.url, error=e)
                return None, better_exception
            else:
                return None, e
        except Exception as e:
            botocore.endpoint.logger.debug("Exception received when sending "
                                           "HTTP request.",
                                           exc_info=True)
            return None, e

        # This returns the http_response and the parsed_data.
        response_dict = yield from convert_to_response_dict(
            http_response, operation_model)
        parser = self._response_parser_factory.create_parser(
            operation_model.metadata['protocol'])
        return ((http_response, parser.parse(response_dict,
                                             operation_model.output_shape)),
                None)


class AioEndpointCreator(EndpointCreator):

    def __init__(self, event_emitter, loop):
        super().__init__(event_emitter)
        self._loop = loop

    def create_endpoint(self, service_model, region_name=None,
                        endpoint_url=None, verify=None,
                        response_parser_factory=None, timeout=DEFAULT_TIMEOUT,
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
            response_parser_factory=response_parser_factory, loop=self._loop,
            connector_args=connector_args)
