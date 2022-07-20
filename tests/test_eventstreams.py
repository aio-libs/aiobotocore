import botocore.parsers
import pytest

from aiobotocore.eventstream import AioEventStream

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


@pytest.mark.moto
@pytest.mark.asyncio
async def test_eventstream_chunking(s3_client):
    # These are the options passed to the EventStream class
    # during a normal run with botocore.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = botocore.parsers.EventStreamXMLParser()
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


@pytest.mark.moto
@pytest.mark.asyncio
async def test_eventstream_no_iter(s3_client):
    # These are the options passed to the EventStream class
    # during a normal run with botocore.
    operation_name = 'SelectObjectContent'
    outputshape = s3_client._service_model.operation_model(
        operation_name
    ).output_shape.members['Payload']
    parser = botocore.parsers.EventStreamXMLParser()
    sr = FakeStreamReader(TEST_STREAM_DATA)

    event_stream = AioEventStream(sr, outputshape, parser, operation_name)

    with pytest.raises(NotImplementedError):
        for _ in event_stream:
            pass
