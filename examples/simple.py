import asyncio
from aiobotocore.session import get_session


AWS_ACCESS_KEY_ID = "xxx"
AWS_SECRET_ACCESS_KEY = "xxx"


async def go():

    bucket = 'dataintake'
    filename = 'dummy.bin'
    folder = 'aiobotocore'
    key = f'{folder}/{filename}'

    session = get_session()
    async with session.create_client(
            's3', region_name='us-west-2',
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            aws_access_key_id=AWS_ACCESS_KEY_ID) as client:
        # upload object to amazon s3
        data = b'\x01' * 1024
        resp = await client.put_object(Bucket=bucket,
                                       Key=key,
                                       Body=data)
        print(resp)

        # getting s3 object properties of file we just uploaded
        resp = await client.get_object_acl(Bucket=bucket, Key=key)
        print(resp)

        # delete object from s3
        resp = await client.delete_object(Bucket=bucket, Key=key)
        print(resp)


if __name__ == '__main__':
    asyncio.run(go())
