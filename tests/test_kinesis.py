import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def kinesis_client(make_create_client, kinesis_server):
    return make_create_client('kinesis', kinesis_server)


@pytest.mark.moto
@pytest.mark.asyncio
async def test_kinesis_send_and_receive(kinesis_client):
    stream_name = 'test-name-stream'
    msg = '{"test": "test json", "f": 1.5}'
    await kinesis_client.create_stream(
        StreamName=stream_name,
        ShardCount=2,
    )
    response = await kinesis_client.put_record(
        StreamName=stream_name,
        Data=msg,
        PartitionKey='0',
    )
    pytest.aio.assert_status_code(response, 200)

    params = {
        'StreamName': stream_name,
        'ShardId': response['ShardId'],
        'StartingSequenceNumber': str(response['SequenceNumber']),
        'ShardIteratorType': 'AT_SEQUENCE_NUMBER',
    }

    shard_iterator = await kinesis_client.get_shard_iterator(**params)

    pytest.aio.assert_status_code(shard_iterator, 200)

    messages = await kinesis_client.get_records(
        ShardIterator=shard_iterator['ShardIterator'],
        Limit=1
    )
    pytest.aio.assert_status_code(messages, 200)

    assert msg == messages['Records'][0]['Data'].decode()


@pytest.mark.moto
@pytest.mark.asyncio
async def test_kinesis_fail_client(kinesis_client):
    with pytest.raises(ClientError):
        await kinesis_client.put_record(
            StreamName='asf',
            Data='',
            PartitionKey='0',
        )

    with pytest.raises(ClientError):
        params = {
            'StreamName': 'asdf',
            'ShardId': '0',
            'StartingSequenceNumber': '1',
            'ShardIteratorType': 'AT_SEQUENCE_NUMBER',
        }

        await kinesis_client.get_shard_iterator(**params)

    with pytest.raises(ClientError):
        await kinesis_client.get_records(
            ShardIterator='c2RmOnNoYXJkSWQtMDAwMDAwMDAwMDAxOjA=',
            Limit=1)
