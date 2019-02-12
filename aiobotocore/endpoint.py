import aiohttp
import asyncio
import io
import wrapt
import botocore.retryhandler
import aiohttp.http_exceptions
from aiohttp.client import URL
from aiohttp.client_reqrep import ClientResponse
from botocore.endpoint import EndpointCreator, Endpoint, DEFAULT_TIMEOUT, \
    MAX_POOL_CONNECTIONS, logger
from botocore.exceptions import ConnectionClosedError
from botocore.hooks import first_non_none_response
from botocore.utils import is_valid_endpoint_url
from botocore.vendored.requests.structures import CaseInsensitiveDict
from botocore.history import get_global_history_recorder
from multidict import MultiDict
from urllib.parse import urlparse

from aiobotocore.response import StreamingBody

MAX_REDIRECTS = 10
history_recorder = get_global_history_recorder()


# Monkey patching: We need to insert the aiohttp exception equivalents
# The only other way to do this would be to have another config file :(
_aiohttp_retryable_exceptions = [
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
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
class _IOBaseWrapper(wrapt.ObjectProxy):
    def close(self):
        # this stream should not be closed by aiohttp, like 1.x
        pass


async def convert_to_response_dict(http_response, operation_model):
    response_dict = {
        # botocore converts keys to str, so make sure that they are in
        # the expected case. See detailed discussion here:
        # https://github.com/aio-libs/aiobotocore/pull/116
        # aiohttp's CIMultiDict camel cases the headers :(
        'headers': CaseInsensitiveDict(
            {k.decode('utf-8').lower(): v.decode('utf-8')
             for k, v in http_response.raw_headers}),
        'status_code': http_response.status_code,
        'context': {
            'operation_name': operation_model.name,
        }
    }

    if response_dict['status_code'] >= 300:
        response_dict['body'] = await http_response.read()
    elif operation_model.has_event_stream_output:
        response_dict['body'] = http_response.raw
    elif operation_model.has_streaming_output:
        length = response_dict['headers'].get('content-length')
        response_dict['body'] = StreamingBody(http_response.raw, length)
    else:
        response_dict['body'] = await http_response.read()
    return response_dict


# This is similar to botocore.response.StreamingBody
class ClientResponseContentProxy(wrapt.ObjectProxy):
    """Proxy object for content stream of http response.  This is here in case
    you want to pass around the "Body" of the response without closing the
    response itself."""

    def __init__(self, response):
        super().__init__(response.__wrapped__.content)
        self._self_response = response

    # Note: we don't have a __del__ method as the ClientResponse has a __del__
    # which will warn the user if they didn't close/release the response
    # explicitly.  A release here would mean reading all the unread data
    # (which could be very large), and a close would mean being unable to re-
    # use the connection, so the user MUST chose.  Default is to warn + close
    async def __aenter__(self):
        await self._self_response.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._self_response.__aexit__(exc_type, exc_val, exc_tb)

    def close(self):
        self._self_response.close()


class ClientResponseProxy(wrapt.ObjectProxy):
    """Proxy object for http response useful for porting from
    botocore underlying http library."""

    def __init__(self, *args, **kwargs):
        super().__init__(ClientResponse(*args, **kwargs))

        # this matches ClientResponse._body
        self._self_body = None

    @property
    def status_code(self):
        return self.status

    @status_code.setter
    def status_code(self, value):
        # botocore tries to set this, see:
        # https://github.com/aio-libs/aiobotocore/issues/190
        # Luckily status is an attribute we can set
        self.status = value

    @property
    def content(self):
        return self._self_body

    @property
    def raw(self):
        return ClientResponseContentProxy(self)

    async def read(self):
        self._self_body = await self.__wrapped__.read()
        return self._self_body


class AioEndpoint(Endpoint):
    def __init__(self, host,
                 endpoint_prefix, event_emitter, proxies=None, verify=True,
                 timeout=DEFAULT_TIMEOUT, response_parser_factory=None,
                 max_pool_connections=MAX_POOL_CONNECTIONS,
                 loop=None, connector_args=None):

        super().__init__(host, endpoint_prefix,
                         event_emitter,
                         response_parser_factory=response_parser_factory)

        self.proxies = proxies

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

        timeout = aiohttp.ClientTimeout(
            sock_connect=self._conn_timeout,
            sock_read=self._read_timeout
        )

        self.verify_ssl = verify
        connector = aiohttp.TCPConnector(
            loop=self._loop,
            limit=max_pool_connections,
            verify_ssl=self.verify_ssl,
            **connector_args)

        self._aio_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            skip_auto_headers={'CONTENT-TYPE'},
            response_class=ClientResponseProxy,
            loop=self._loop,
            auto_decompress=False)

    async def _request(self, method, url, headers, data, verify, stream):
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
        # https://github.com/boto/botocore/issues/1255
        headers['Accept-Encoding'] = 'identity'
        headers_ = MultiDict(
            (z[0], text_(z[1], encoding='utf-8')) for z in headers.items())

        # botocore does this during the request so we do this here as well
        proxy = self.proxies.get(urlparse(url.lower()).scheme)

        if isinstance(data, io.IOBase):
            data = _IOBaseWrapper(data)

        url = URL(url, encoded=True)
        resp = await self._aio_session.request(
            method, url=url, headers=headers_, data=data, proxy=proxy,
            verify_ssl=verify)

        # If we're not streaming, read the content so we can retry any timeout
        #  errors, see:
        # https://github.com/boto/botocore/blob/develop/botocore/vendored/requests/sessions.py#L604
        if not stream:
            await resp.read()

        return resp

    async def _send_request(self, request_dict, operation_model):
        attempts = 1
        request = self.create_request(request_dict, operation_model)
        context = request_dict['context']
        success_response, exception = await self._get_response(
            request, operation_model, context)
        while (await self._needs_retry(attempts, operation_model,
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
            success_response, exception = await self._get_response(
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
    async def _needs_retry(self, attempts, operation_model, request_dict,
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
            await asyncio.sleep(handler_response, loop=self._loop)
            return True

    async def _get_response(self, request, operation_model, context):
        # This will return a tuple of (success_response, exception)
        # and success_response is itself a tuple of
        # (http_response, parsed_dict).
        # If an exception occurs then the success_response is None.
        # If no exception occurs then exception is None.
        # If no exception occurs then exception is None.
        success_response, exception = await self._do_get_response(
            request, operation_model)
        kwargs_to_emit = {
            'response_dict': None,
            'parsed_response': None,
            'context': context,
            'exception': exception,
        }
        if success_response is not None:
            http_response, parsed_response = success_response
            kwargs_to_emit['parsed_response'] = parsed_response
            kwargs_to_emit['response_dict'] = await convert_to_response_dict(
                http_response, operation_model)
        service_id = operation_model.service_model.service_id.hyphenize()
        self._event_emitter.emit(
            'response-received.%s.%s' % (
                service_id, operation_model.name), **kwargs_to_emit)
        return success_response, exception

    async def _do_get_response(self, request, operation_model):
        try:
            # http request substituted too async one
            logger.debug("Sending http request: %s", request)
            history_recorder.record('HTTP_REQUEST', {
                'method': request.method,
                'headers': request.headers,
                'streaming': operation_model.has_streaming_input,
                'url': request.url,
                'body': request.body
            })

            service_id = operation_model.service_model.service_id.hyphenize()
            event_name = 'before-send.%s.%s' % \
                         (service_id, operation_model.name)
            responses = self._event_emitter.emit(event_name, request=request)
            http_response = first_non_none_response(responses)

            if http_response is None:
                streaming = any([
                    operation_model.has_streaming_output,
                    operation_model.has_event_stream_output
                ])
                http_response = await self._request(
                    request.method, request.url, request.headers, request.body,
                    verify=self.verify_ssl,
                    stream=streaming)
        except aiohttp.ClientConnectionError as e:
            e.request = request  # botocore expects the request property

            # For a connection error, if it looks like it's a DNS
            # lookup issue, 99% of the time this is due to a misconfigured
            # region/endpoint so we'll raise a more specific error message
            # to help users.
            logger.debug("ConnectionError received when sending HTTP request.",
                         exc_info=True)

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
        response_dict = await convert_to_response_dict(http_response,
                                                       operation_model)

        http_response_record_dict = response_dict.copy()
        http_response_record_dict['streaming'] = \
            operation_model.has_streaming_output
        history_recorder.record('HTTP_RESPONSE', http_response_record_dict)

        protocol = operation_model.metadata['protocol']
        parser = self._response_parser_factory.create_parser(protocol)
        parsed_response = parser.parse(
            response_dict, operation_model.output_shape)
        history_recorder.record('PARSED_RESPONSE', parsed_response)
        return (http_response, parsed_response), None


class AioEndpointCreator(EndpointCreator):
    def __init__(self, event_emitter, loop):
        super().__init__(event_emitter)
        self._loop = loop

    def create_endpoint(self, service_model, region_name=None,
                        endpoint_url=None, verify=None,
                        response_parser_factory=None, timeout=DEFAULT_TIMEOUT,
                        max_pool_connections=MAX_POOL_CONNECTIONS,
                        proxies=None, connector_args=None):
        if not is_valid_endpoint_url(endpoint_url):
            raise ValueError("Invalid endpoint: %s" % endpoint_url)
        if proxies is None:
            proxies = self._get_proxies(endpoint_url)
        return AioEndpoint(
            endpoint_url,
            endpoint_prefix=service_model.endpoint_prefix,
            event_emitter=self._event_emitter,
            proxies=proxies,
            verify=self._get_verify_value(verify),
            timeout=timeout,
            max_pool_connections=max_pool_connections,
            response_parser_factory=response_parser_factory,
            loop=self._loop, connector_args=connector_args)
