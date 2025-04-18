from botocore.parsers import (
    LOG,
    EC2QueryParser,
    JSONParser,
    NoInitialResponseError,
    QueryParser,
    ResponseParser,
    ResponseParserError,
    ResponseParserFactory,
    RestJSONParser,
    RestXMLParser,
    lowercase_dict,
)

from .eventstream import AioEventStream


class AioResponseParserFactory(ResponseParserFactory):
    def create_parser(self, protocol_name):
        parser_cls = PROTOCOL_PARSERS[protocol_name]
        return parser_cls(**self._defaults)


def create_parser(protocol):
    return AioResponseParserFactory().create_parser(protocol)


class AioResponseParser(ResponseParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


class AioQueryParser(QueryParser, AioResponseParser):
    pass


class AioEC2QueryParser(EC2QueryParser, AioResponseParser):
    pass


class AioJSONParser(JSONParser, AioResponseParser):
    async def _do_parse(self, response, shape):
        parsed = {}
        if shape is not None:
            event_name = shape.event_stream_name
            if event_name:
                parsed = await self._handle_event_stream(
                    response, shape, event_name
                )
            else:
                parsed = self._handle_json_body(response['body'], shape)
        self._inject_response_metadata(parsed, response['headers'])
        return parsed

    async def _handle_event_stream(self, response, shape, event_name):
        event_stream_shape = shape.members[event_name]
        event_stream = self._create_event_stream(response, event_stream_shape)
        try:
            event = await event_stream.get_initial_response()
        except NoInitialResponseError:
            error_msg = 'First event was not of type initial-response'
            raise ResponseParserError(error_msg)
        parsed = self._handle_json_body(event.payload, shape)
        parsed[event_name] = event_stream
        return parsed

    # this is actually from ResponseParser however for now JSONParser is the
    # only class that needs this async
    async def parse(self, response, shape):
        LOG.debug('Response headers: %s', response['headers'])
        LOG.debug('Response body:\n%s', response['body'])
        if response['status_code'] >= 301:
            if self._is_generic_error_response(response):
                parsed = self._do_generic_error_parse(response)
            elif self._is_modeled_error_shape(shape):
                parsed = self._do_modeled_error_parse(response, shape)
                # We don't want to decorate the modeled fields with metadata
                return parsed
            else:
                parsed = self._do_error_parse(response, shape)
        else:
            parsed = await self._do_parse(response, shape)

        # We don't want to decorate event stream responses with metadata
        if shape and shape.serialization.get('eventstream'):
            return parsed

        # Add ResponseMetadata if it doesn't exist and inject the HTTP
        # status code and headers from the response.
        if isinstance(parsed, dict):
            response_metadata = parsed.get('ResponseMetadata', {})
            response_metadata['HTTPStatusCode'] = response['status_code']
            # Ensure that the http header keys are all lower cased. Older
            # versions of urllib3 (< 1.11) would unintentionally do this for us
            # (see urllib3#633). We need to do this conversion manually now.
            headers = response['headers']
            response_metadata['HTTPHeaders'] = lowercase_dict(headers)
            parsed['ResponseMetadata'] = response_metadata
            self._add_checksum_response_metadata(response, response_metadata)
        return parsed


class AioRestJSONParser(RestJSONParser, AioResponseParser):
    pass


class AioRestXMLParser(RestXMLParser, AioResponseParser):
    pass


PROTOCOL_PARSERS = {
    'ec2': AioEC2QueryParser,
    'query': AioQueryParser,
    'json': AioJSONParser,
    'rest-json': AioRestJSONParser,
    'rest-xml': AioRestXMLParser,
}
