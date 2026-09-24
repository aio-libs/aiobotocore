from contextlib import AsyncExitStack

import anyio
import pytest

from aiobotocore._httpx import httpx
from aiobotocore.endpoint import convert_to_response_dict
from aiobotocore.eventstream import AioEventStream
from aiobotocore.parsers import AioEventStreamXMLParser
from aiobotocore.response import AioHttpxEventStreamRawStream

# TODO once Moto supports either S3 Select or Kinesis SubscribeToShard then
# this can be tested against a real AWS API


TEST_STREAM_DATA = (
    b'\x00\x00\x00w\x00\x00\x00U5\xd1F\xcd\r:message-type\x07\x00\x05event\x0b:event-'
    b'type\x07\x00\x07Records\r:content-type\x07\x00\x18application/octet-stream{"hel'
    b'lo":"world"}\nF\x0e\x9a2',
    b'\x00\x00\x00\xce\x00\x00\x00C\xdc\xd2\x99\xf9\r:message-type\x07\x00\x05event'
    b'\x0b:event-type\x07\x00\x05Stats\r:content-type\x07\x00\x08text/xml<Stats xml'
    b'ns=""><BytesScanned>19</BytesScanned><BytesProcessed>19</BytesProcessed><Byte'
    b'sReturned>18</BytesReturned></Stats>\x92\xd0?\xa5\x00\x00\x008\x00\x00\x00(\xc1'
    b'\xc6\x84\xd4\r:message-type\x07\x00\x05event\x0b:event-type\x07\x00\x03End\xcf'
    b'\x97\xd3\x92',
)


class FakeStreamReader:
    class ChunkedIterator:
        def __init__(self, chunks):
            self.iter = iter(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                result = next(self.iter)
                return result, True
            except StopIteration:
                raise StopAsyncIteration()

    def __init__(self, chunks):
        self.chunks = chunks
        self.content = self

    def iter_chunks(self):
        return self.ChunkedIterator(self.chunks)


async def test_eventstream_chunking(s3_client):
    # These are the options passed to the EventStream class
    # during a normal run with botocore.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = AioEventStreamXMLParser()
    sr = FakeStreamReader(TEST_STREAM_DATA)

    event_stream = AioEventStream(sr, outputshape, parser, operation_name)

    events = []
    # {'Records': {'Payload': b'{"hello":"world"}\n'}}
    # {'Stats': {'Details': {'BytesScanned': 19,
    #                        'BytesProcessed': 19,
    #                        'BytesReturned': 18}}}
    # {'End': {}}
    async for event in event_stream:
        events.append(event)

    assert len(events) == 3
    event1, event2, event3 = events

    assert 'Records' in event1
    assert 'Stats' in event2
    assert 'End' in event3


async def test_eventstream_no_iter(s3_client):
    # These are the options passed to the EventStream class
    # during a normal run with botocore.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = AioEventStreamXMLParser()
    sr = FakeStreamReader(TEST_STREAM_DATA)

    event_stream = AioEventStream(sr, outputshape, parser, operation_name)

    with pytest.raises(NotImplementedError):
        for _ in event_stream:
            pass


@pytest.mark.localonly
async def test_kinesis_stream_json_parser(
    exit_stack: AsyncExitStack, kinesis_client, create_stream
):
    # unfortunately moto doesn't support kinesis register_stream_consumer +
    # subscribe_to_shard yet
    stream_name = await create_stream(ShardCount=1)

    describe_response = await kinesis_client.describe_stream(
        StreamName=stream_name
    )

    shard_id = describe_response["StreamDescription"]["Shards"][0]["ShardId"]
    stream_arn = describe_response["StreamDescription"]["StreamARN"]

    consumer_arn = None
    consumer_name = 'consumer'

    # Create some data
    keys = [str(i) for i in range(1, 5)]
    for k in keys:
        await kinesis_client.put_record(
            StreamName=stream_name, Data=k, PartitionKey=k
        )

    register_response = await kinesis_client.register_stream_consumer(
        StreamARN=stream_arn, ConsumerName=consumer_name
    )
    consumer_arn = register_response['Consumer']['ConsumerARN']

    while (
        describe_response := (
            await kinesis_client.describe_stream_consumer(  # noqa: E231, E999, E251, E501
                StreamARN=stream_arn,
                ConsumerName=consumer_name,
                ConsumerARN=consumer_arn,
            )
        )
    ) and describe_response['ConsumerDescription'][
        'ConsumerStatus'
    ] == 'CREATING':
        print("Waiting for stream consumer creation")
        await anyio.sleep(1)

    starting_position = {'Type': 'LATEST'}
    subscribe_response = await kinesis_client.subscribe_to_shard(
        ConsumerARN=consumer_arn,
        ShardId=shard_id,
        StartingPosition=starting_position,
    )
    async for event in subscribe_response['EventStream']:
        assert event['SubscribeToShardEvent']['Records'] == []
        break


def _httpx_streaming_response(chunks):
    async def body():
        for chunk in chunks:
            yield chunk

    return httpx.Response(200, content=body())


class _EventStreamOperationModel:
    name = 'SelectObjectContent'
    has_event_stream_output = True


class _HttpResponse:
    """Minimal stand-in for the AIOHttp/Httpx response wrapper: only what
    convert_to_response_dict reads on the event stream path."""

    def __init__(self, raw):
        self.raw = raw
        self.status_code = 200
        self.headers = {'content-type': 'application/vnd.amazon.eventstream'}


async def test_eventstream_httpx_raw_stream_adapter(s3_client):
    # On the httpx backend the event stream body is an unread httpx.Response;
    # it used to be passed to AioEventStream as-is, whose aiohttp-style
    # raw.content.iter_chunks() access raised httpx.ResponseNotRead. The
    # adapter must now be selected in convert_to_response_dict and drive the
    # same three events the aiohttp FakeStreamReader path yields.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = AioEventStreamXMLParser()

    raw = _httpx_streaming_response(TEST_STREAM_DATA)
    response_dict = await convert_to_response_dict(
        _HttpResponse(raw), _EventStreamOperationModel()
    )
    assert isinstance(response_dict['body'], AioHttpxEventStreamRawStream)

    event_stream = AioEventStream(
        response_dict['body'], outputshape, parser, operation_name
    )
    events = []
    async for event in event_stream:
        events.append(event)

    assert len(events) == 3
    assert 'Records' in events[0]
    assert 'Stats' in events[1]
    assert 'End' in events[2]


async def test_eventstream_aiohttp_raw_stream_passthrough(s3_client):
    # Non-httpx raw streams keep flowing through untouched.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = AioEventStreamXMLParser()

    sr = FakeStreamReader(TEST_STREAM_DATA)
    response_dict = await convert_to_response_dict(
        _HttpResponse(sr), _EventStreamOperationModel()
    )
    assert response_dict['body'] is sr

    event_stream = AioEventStream(
        response_dict['body'], outputshape, parser, operation_name
    )
    events = [event async for event in event_stream]
    assert len(events) == 3
