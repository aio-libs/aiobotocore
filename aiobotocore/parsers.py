from botocore.parsers import ResponseParserFactory, RestXMLParser, \
    RestJSONParser, JSONParser, QueryParser, EC2QueryParser
from .eventstream import AioEventStream


class AioRestXMLParser(RestXMLParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


class AioEC2QueryParser(EC2QueryParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


class AioQueryParser(QueryParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


class AioJSONParser(JSONParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


class AioRestJSONParser(RestJSONParser):
    def _create_event_stream(self, response, shape):
        parser = self._event_stream_parser
        name = response['context'].get('operation_name')
        return AioEventStream(response['body'], shape, parser, name)


PROTOCOL_PARSERS = {
    'ec2': AioEC2QueryParser,
    'query': AioQueryParser,
    'json': AioJSONParser,
    'rest-json': AioRestJSONParser,
    'rest-xml': AioRestXMLParser,
}


class AioResponseParserFactory(ResponseParserFactory):
    def create_parser(self, protocol_name):
        parser_cls = PROTOCOL_PARSERS[protocol_name]
        return parser_cls(**self._defaults)
