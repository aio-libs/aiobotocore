import asyncio
import aiobotocore

AWS_ACCESS_KEY_ID = "xxx"
AWS_SECRET_ACCESS_KEY = "xxx"


@asyncio.coroutine
def go(loop):

    bucket = 'dataintake'
    filename = 'dummy.bin'
    folder = 'aiobotocore'
    key = '{}/{}'.format(folder, filename)

    session = aiobotocore.get_session(loop=loop)
    client = session.create_client('s3', region_name='us-west-2',
                                   endpoint_url='http://127.0.0.1:5000',
                                   aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                                   aws_access_key_id=AWS_ACCESS_KEY_ID)
    # upload object to amazon s3
    data = b'\x01'*1024
    resp = yield from client.put_object(Bucket=bucket,
                                        Key=key,
                                        Body=data)
    print(resp)

    # getting s3 object properties of file we just uploaded
    resp = yield from client.get_object_acl(Bucket=bucket, Key=key)
    print(resp)

    # delete object from s3
    resp = yield from client.delete_object(Bucket=bucket, Key=key)
    print(resp)


loop = asyncio.get_event_loop()
loop.run_until_complete(go(loop))
