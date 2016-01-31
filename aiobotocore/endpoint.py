import asyncio
import aiohttp
from aiohttp.client_reqrep import ClientResponse

from botocore.utils import is_valid_endpoint_url
from botocore.endpoint import EndpointCreator, Endpoint, DEFAULT_TIMEOUT
from botocore.exceptions import EndpointConnectionError, \
    BaseEndpointResolverError


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
    """Proxy object for content stream of http response"""

    def __init__(self, response):
        self.__response = response
        self.__content = self.__response.content

    def __getattr__(self, item):
        return getattr(self.__content, item)

    def __dir__(self):
        attrs = dir(self.__content)
        attrs.append('close')
        return attrs

    def close(self):
        self.__response.close()

class ClientResponseProxy:
    """Proxy object for http response useful for porting from
    botocore underlying http library."""

    def __init__(self, *args, **kwargs):
        self._impl = ClientResponse(*args, **kwargs)
        self._body = None

    def __getattr__(self, item):
        if item == 'status_code':
            return getattr(self._impl, 'status')
        if item == 'content':
            return self._body
        if item == 'raw':
            return ClientResponseContentProxy(self._impl)

        return getattr(self._impl, item)

    @asyncio.coroutine
    def read(self):
        self._body = yield from self._impl.read()
        return self._body


class AioEndpoint(Endpoint):

    def __init__(self, host,
                 endpoint_prefix, event_emitter, proxies=None, verify=True,
                 timeout=DEFAULT_TIMEOUT, response_parser_factory=None,
                 loop=None):

        super().__init__(host, endpoint_prefix,
                         event_emitter, proxies=proxies, verify=verify,
                         timeout=timeout,
                         response_parser_factory=response_parser_factory)

        self._loop = loop or asyncio.get_event_loop()
        
        # AWS has a 20 second idle timeout: https://forums.aws.amazon.com/message.jspa?messageID=215367
        # and aiohttp default timeout is 30s so we set it to something reasonable here
        self._aio_seesion = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(loop=self._loop, keepalive_timeout=12),
            skip_auto_headers={'CONTENT-TYPE'},
            response_class=ClientResponseProxy, loop=self._loop)

    @asyncio.coroutine
    def _request(self, method, url, headers, data):

        headers_ = dict(
            (z[0], text_(z[1], encoding='utf-8')) for z in headers.items())
        resp = yield from self._aio_seesion.request(
            method, url=url, headers=headers_, data=data)
        return resp

    @asyncio.coroutine
    def _send_request(self, request_dict, operation_model):
        attempts = 1
        request = self.create_request(request_dict, operation_model)
        success_response, exception = yield from self._get_response(
            request, operation_model, attempts)
        while self._needs_retry(attempts, operation_model,
                                success_response, exception):
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

    @asyncio.coroutine
    def _get_response(self, request, operation_model, attempts):
        # This will return a tuple of (success_response, exception)
        # and success_response is itself a tuple of
        # (http_response, parsed_dict).
        # If an exception occurs then the success_response is None.
        # If no exception occurs then exception is None.
        try:
            # http request substituted too async one
            resp = yield from self._request(
                request.method, request.url, request.headers, request.body)
            http_response = resp

        except ConnectionError as e:
            # For a connection error, if it looks like it's a DNS
            # lookup issue, 99% of the time this is due to a misconfigured
            # region/endpoint so we'll raise a more specific error message
            # to help users.
            if self._looks_like_dns_error(e):
                endpoint_url = request.url
                better_exception = EndpointConnectionError(
                    endpoint_url=endpoint_url, error=e)
                return (None, better_exception)
            else:
                return (None, e)
        except Exception as e:
            # logger.debug("Exception received when sending HTTP request.",
            #              exc_info=True)
            return (None, e)

        # This returns the http_response and the parsed_data.
        response_dict = yield from convert_to_response_dict(
            http_response, operation_model)
        parser = self._response_parser_factory.create_parser(
            operation_model.metadata['protocol'])
        return ((http_response, parser.parse(response_dict,
                                             operation_model.output_shape)),
                None)


class AioEndpointCreator(EndpointCreator):

    def __init__(self, endpoint_resolver, configured_region, event_emitter,
                 loop):
        super().__init__(endpoint_resolver, configured_region, event_emitter)
        self._loop = loop

    def create_endpoint(self, service_model, region_name=None, is_secure=True,
                        endpoint_url=None, verify=None,
                        response_parser_factory=None, timeout=DEFAULT_TIMEOUT):
        if region_name is None:
            region_name = self._configured_region
        # Use the endpoint resolver heuristics to build the endpoint url.
        scheme = 'https' if is_secure else 'http'
        try:
            endpoint = self._endpoint_resolver.construct_endpoint(
                service_model.endpoint_prefix,
                region_name, scheme=scheme)
        except BaseEndpointResolverError:
            if endpoint_url is not None:
                # If the user provides an endpoint_url, it's ok
                # if the heuristics didn't find anything.  We use the
                # user provided endpoint_url.
                endpoint = {'uri': endpoint_url, 'properties': {}}
            else:
                raise

        if endpoint_url is not None:
            # If the user provides an endpoint url, we'll use that
            # instead of what the heuristics rule gives us.
            final_endpoint_url = endpoint_url
        else:
            final_endpoint_url = endpoint['uri']
        if not is_valid_endpoint_url(final_endpoint_url):
            raise ValueError("Invalid endpoint: %s" % final_endpoint_url)

        proxies = self._get_proxies(final_endpoint_url)
        verify_value = self._get_verify_value(verify)
        return AioEndpoint(
            final_endpoint_url,
            endpoint_prefix=service_model.endpoint_prefix,
            event_emitter=self._event_emitter,
            proxies=proxies,
            verify=verify_value,
            timeout=timeout,
            response_parser_factory=response_parser_factory, loop=self._loop)
