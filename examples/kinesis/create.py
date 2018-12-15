# Boto should get credentials from ~/.aws/credentials or the environment
import asyncio

import aiobotocore


def client(name, loop, **kwargs):
    session = aiobotocore.get_session(loop=loop)
    return session.create_client(name, **kwargs)


async def create_stream(loop, stream_name, shard_count=1, **kwargs):
    """
    CreateStream is an asynchronous operation.
    Upon receiving a CreateStream request, Kinesis Data Streams immediately
    returns and sets the stream status to CREATING .
    After the stream is created,
    Kinesis Data Streams sets the stream status to ACTIVE .
    You should perform read and write operations only on an ACTIVE stream.
    """

    async with client('kinesis', loop, **kwargs) as kinesis:
        await kinesis.create_stream(
            StreamName=stream_name,
            ShardCount=shard_count
        )


def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            create_stream(
                loop=loop,
                stream_name='test_stream',
                region_name='us-west-2'
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
