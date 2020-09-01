import asyncio
from aiobotocore.session import AioSession
import os
import logging

logging.basicConfig(level=logging.DEBUG)

for key in {'AWS_REGION', 'AWS_DEFAULT_REGION'}:
    value = os.environ.get(key)
    if value:
        del os.environ[key]


async def main():
    session = AioSession()
    async with session.create_client('s3') as client:
        response = await client.head_object(Bucket='thehesiod-temp', Key='dummy')
        print(response)

asyncio.run(main())
