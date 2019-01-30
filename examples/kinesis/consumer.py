#!/usr/bin/env python3
"""
aiobotocore Kinesis Consumer Example
"""

import asyncio

import aiobotocore


def client(name, loop, **kwargs):
    session = aiobotocore.get_session(loop=loop)
    return session.create_client(name, **kwargs)


async def get_shards(kinesis, stream_name, save_shards=None):
    """
    I want to receive for one consumer of all the message from all shards

    save_shards - contains information from which shard
                    and after which message to receive
                    {
                        'shardId-000000000000': '49590165....615450',
                        'shardId-000000000001': '35908624....056898',
                    }
    """
    shards = []

    streams = await kinesis.describe_stream(StreamName=stream_name)
    if streams['StreamStatus'] != 'ACTIVE':
        raise RuntimeError('Stream {stream_name} status {status}'.format(
            stream_name=stream_name,
            status=streams['StreamStatus'],
        ))

    for shard in streams['StreamDescription']['Shards']:
        shard_id = shard['ShardId']
        params = {
            'StreamName': stream_name,
            'ShardId': shard_id,
            'ShardIteratorType': 'LATEST'
        }

        next_iter = save_shards.get(shard_id, None)
        if next_iter:
            params['StartingSequenceNumber'] = next_iter
            params['ShardIteratorType'] = 'AFTER_SEQUENCE_NUMBER'

        shard_iterator = await kinesis.get_shard_iterator(**params)
        shards.append((shard_id, shard_iterator['ShardIterator']))

    return shards


async def receive(loop, stream_name, fn, limit=10, save_shards=None, **kwargs):
    async with client('kinesis', loop, **kwargs) as kinesis:
        shards = await get_shards(kinesis, stream_name, save_shards)
        while True:
            await asyncio.sleep(0.2)

            shard_id, shard_it = shards.pop(0)
            messages = await kinesis.get_records(
                ShardIterator=shard_it,
                Limit=limit
            )
            shards.append((shard_id, messages["NextShardIterator"]))

            if not messages['Records']:
                continue

            for message in messages["Records"]:
                try:
                    await fn(shard_id, message)
                except Exception as e:
                    print(e)


async def handler(shard_id, message):
    """
    async coroutine for handler message

    :param shard_id:
    :type shard_id: str
    :param message:
        {
            'Records': [
                {
                    'SequenceNumber': 'string unique identifier',
                    'ApproximateArrivalTimestamp': datetime(2015, 1, 1),
                    'Data': b'bytes',
                    'PartitionKey': 'string',
                    'EncryptionType': 'NONE'|'KMS'
                },
            ],
            'NextShardIterator': 'string',
            'MillisBehindLatest': 123
        }
    :type message: dict
    :return:
    """

    print(shard_id, message)


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            receive(
                loop=loop,
                stream_name='test_stream',
                fn=handler,
                region_name='us-west-2',
                aws_access_key_id='xxxxxxxxxxxx',
                aws_secret_access_key='xxxxxxxx'
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
