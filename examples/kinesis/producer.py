#!/usr/bin/env python3
"""
aiobotocore Kinesis Producer Example
"""
import asyncio

import aiobotocore


def client(name, loop, **kwargs):
    session = aiobotocore.get_session(loop=loop)
    return session.create_client(name, **kwargs)


async def send(msg, loop, stream_name, partition_key, **kwargs):
    async with client('kinesis', loop, **kwargs) as kinesis:
        resp = await kinesis.put_record(
            StreamName=stream_name,
            Data=msg,
            PartitionKey=partition_key
        )

    print('response send:', resp)


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            send(
                msg='{"test": "test json", "f": 1.5}',
                loop=loop,
                stream_name='test_stream',
                partition_key='0',
                region_name='us-west-2',
                aws_access_key_id='xxxxxxxxxxxx',
                aws_secret_access_key='xxxxxxxx'
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
