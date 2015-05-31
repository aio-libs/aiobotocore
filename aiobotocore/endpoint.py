import asyncio
import aiohttp
from aiohttp.client_reqrep import ClientResponse
import botocore.endpoint
from botocore.endpoint import _get_proxies, _get_verify_value, DEFAULT_TIMEOUT
from botocore.exceptions import EndpointConnectionError


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
            return getattr(self._impl, 'content')

        return getattr(self._impl, item)

    @asyncio.coroutine
    def read(self):
        self._body = yield from self._impl.read()
        return self._body


class AioEndpoint(botocore.endpoint.Endpoint):

    def __init__(self, region_name, host, user_agent,
                 endpoint_prefix, event_emitter, proxies=None, verify=True,
                 timeout=DEFAULT_TIMEOUT, response_parser_factory=None,
                 loop=None):

        super().__init__(region_name, host, user_agent, endpoint_prefix,
                         event_emitter, proxies=proxies, verify=verify,
                         timeout=timeout,
                         response_parser_factory=response_parser_factory)

        self._loop = loop or asyncio.get_event_loop()
        self._connector = aiohttp.TCPConnector(loop=self._loop)

    @asyncio.coroutine
    def _request(self, method, url, headers, data):

        headers_ = dict(
            (z[0], text_(z[1], encoding='utf-8')) for z in headers.items())

        resp = yield from aiohttp.request(method,
                                          url=url,
                                          headers=headers_,
                                          data=data,
                                          connector=self._connector,
                                          loop=self._loop,
                                          response_class=ClientResponseProxy)
        return resp

    @asyncio.coroutine
    def _send_request(self, request_dict, operation_model):
        # TODO: fix this
        request_dict['headers']['CONTENT-TYPE'] = 'application/octet-stream'
        attempts = 1
        request = self.create_request(request_dict, operation_model)
        success_response, exception = yield from self._get_response(
            request, operation_model, attempts)

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

        response_dict = yield from convert_to_response_dict(
            http_response, operation_model)

        parser = self._response_parser_factory.create_parser(
            operation_model.metadata['protocol'])
        return ((http_response, parser.parse(response_dict,
                                             operation_model.output_shape)),
                None)


class AioEndpointCreator(botocore.endpoint.EndpointCreator):

    def __init__(self, endpoint_resolver, configured_region, event_emitter,
                 user_agent, loop):
        super().__init__(endpoint_resolver, configured_region, event_emitter,
                         user_agent)
        self._loop = loop

    def _get_endpoint(self, service_model, region_name, endpoint_url,
                      verify, response_parser_factory):
        service_name = service_model.signing_name
        endpoint_prefix = service_model.endpoint_prefix
        user_agent = self._user_agent
        event_emitter = self._event_emitter
        user_agent = self._user_agent
        return get_endpoint_complex(service_name, endpoint_prefix,
                                    region_name, endpoint_url,
                                    verify, user_agent, event_emitter,
                                    response_parser_factory, loop=self._loop)


def get_endpoint_complex(service_name, endpoint_prefix,
                         region_name, endpoint_url, verify,
                         user_agent, event_emitter,
                         response_parser_factory=None, loop=None):
    proxies = _get_proxies(endpoint_url)
    verify = _get_verify_value(verify)
    return AioEndpoint(
        region_name, endpoint_url,
        user_agent=user_agent,
        endpoint_prefix=endpoint_prefix,
        event_emitter=event_emitter,
        proxies=proxies,
        verify=verify,
        response_parser_factory=response_parser_factory,
        loop=loop)
