import asyncio
from contextlib import AsyncExitStack

import pytest

from aiobotocore.eventstream import AioEventStream
from aiobotocore.parsers import AioEventStreamXMLParser

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
        await asyncio.sleep(1)

    starting_position = {'Type': 'LATEST'}
    subscribe_response = await kinesis_client.subscribe_to_shard(
        ConsumerARN=consumer_arn,
        ShardId=shard_id,
        StartingPosition=starting_position,
    )
    async for event in subscribe_response['EventStream']:
        assert event['SubscribeToShardEvent']['Records'] == []
        break
