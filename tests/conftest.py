import asyncio
import random
import string
import aiobotocore
from aiobotocore.config import AioConfig
from itertools import chain
import tempfile
import os

# Third Party
import pytest
import aiohttp


host = '127.0.0.1'

_PYCHARM_HOSTED = os.environ.get('PYCHARM_HOSTED') == '1'


@pytest.fixture(scope="session", params=[True, False],
                ids=['debug[true]', 'debug[false]'])
def debug(request):
    return request.param


def random_bucketname():
    # 63 is the max bucket length.
    return random_name()


def random_tablename():
    return random_name()


def random_name():
    """Return a string with presumably unique contents

    The string contains only symbols allowed for s3 buckets
    (alphanumeric, dot and hyphen).
    """
    return ''.join(random.sample(string.ascii_letters, k=40))


def assert_status_code(response, status_code):
    assert response['ResponseMetadata']['HTTPStatusCode'] == status_code


async def assert_num_uploads_found(
        s3_client, bucket_name, operation, num_uploads, *, max_items=None,
        num_attempts=5):
    paginator = s3_client.get_paginator(operation)
    for _ in range(num_attempts):
        pages = paginator.paginate(Bucket=bucket_name,
                                   PaginationConfig={'MaxItems': max_items})
        responses = []
        async for page in pages:
            responses.append(page)

        # It sometimes takes a while for all the uploads to show up,
        # especially if the upload was just created.  If we don't
        # see the expected amount, we retry up to num_attempts time
        # before failing.
        amount_seen = len(responses[0]['Uploads'])
        if amount_seen == num_uploads:
            # Test passed.
            return
        else:
            # Sleep and try again.
            await asyncio.sleep(2)

        pytest.fail("Expected to see %s uploads, instead saw: %s" % (
            num_uploads, amount_seen))


@pytest.fixture
def aa_fail_proxy_config(monkeypatch):
    # NOTE: name of this fixture must be alphabetically first to run first
    monkeypatch.setenv('HTTP_PROXY', 'http://{}:54321'.format(host))
    monkeypatch.setenv('HTTPS_PROXY', 'http://{}:54321'.format(host))


@pytest.fixture
def aa_succeed_proxy_config(monkeypatch):
    # NOTE: name of this fixture must be alphabetically first to run first
    monkeypatch.setenv('HTTP_PROXY', 'http://{}:54321'.format(host))
    monkeypatch.setenv('HTTPS_PROXY', 'http://{}:54321'.format(host))

    # this will cause us to skip proxying
    monkeypatch.setenv('NO_PROXY', 'amazonaws.com')


@pytest.fixture
def session():
    session = aiobotocore.session.AioSession()
    return session


@pytest.fixture
def region():
    return 'us-east-1'


@pytest.fixture
def alternative_region():
    return 'us-west-2'


@pytest.fixture
def signature_version():
    return 's3'


@pytest.fixture
def config(region, signature_version):
    connect_timeout = read_timout = 5
    if _PYCHARM_HOSTED:
        connect_timeout = read_timout = 180

    return AioConfig(region_name=region, signature_version=signature_version,
                     read_timeout=read_timout, connect_timeout=connect_timeout)


@pytest.fixture
def mocking_test():
    # change this flag for test with real aws
    # TODO: this should be merged with pytest.mark.moto
    return True


def moto_config(endpoint_url):
    kw = dict(endpoint_url=endpoint_url,
              aws_secret_access_key="xxx",
              aws_access_key_id="xxx")

    return kw


@pytest.fixture
async def s3_client(session, region, config, s3_server, mocking_test):
    kw = moto_config(s3_server) if mocking_test else {}

    async with session.create_client('s3', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def alternative_s3_client(session, alternative_region, signature_version,
                                s3_server, mocking_test):
    kw = moto_config(s3_server) if mocking_test else {}

    config = AioConfig(
        region_name=alternative_region, signature_version=signature_version,
        read_timeout=5, connect_timeout=5)

    async with session.create_client('s3', region_name=alternative_region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def dynamodb_client(session, region, config, dynamodb2_server,
                          mocking_test):
    kw = moto_config(dynamodb2_server) if mocking_test else {}
    async with session.create_client('dynamodb', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def cloudformation_client(session, region, config, cloudformation_server,
                                mocking_test):
    kw = moto_config(cloudformation_server) if mocking_test else {}
    async with session.create_client('cloudformation', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def sns_client(session, region, config, sns_server, mocking_test):
    kw = moto_config(sns_server) if mocking_test else {}
    async with session.create_client('sns', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def sqs_client(session, region, config, sqs_server, mocking_test):
    kw = moto_config(sqs_server) if mocking_test else {}
    async with session.create_client('sqs', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def batch_client(session, region, config, batch_server, mocking_test):
    kw = moto_config(batch_server) if mocking_test else {}
    async with session.create_client('batch', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def lambda_client(session, region, config, lambda_server, mocking_test):
    kw = moto_config(lambda_server) if mocking_test else {}
    async with session.create_client('lambda', region_name=region,
                                     config=config, **kw) as client:
        yield client


@pytest.fixture
async def iam_client(session, region, config, iam_server, mocking_test):
    kw = moto_config(iam_server) if mocking_test else {}
    async with session.create_client('iam', region_name=region,
                                     config=config, **kw) as client:
        yield client


async def recursive_delete(s3_client, bucket_name):
    # Recursively deletes a bucket and all of its contents.
    paginator = s3_client.get_paginator('list_object_versions')
    async for n in paginator.paginate(
            Bucket=bucket_name, Prefix=''):
        for obj in chain(
                n.get('Versions', []),
                n.get('DeleteMarkers', []),
                n.get('Contents', []),
                n.get('CommonPrefixes', [])):
            kwargs = dict(Bucket=bucket_name, Key=obj['Key'])
            if 'VersionId' in obj:
                kwargs['VersionId'] = obj['VersionId']
            resp = await s3_client.delete_object(**kwargs)
            assert_status_code(resp, 204)

    resp = await s3_client.delete_bucket(Bucket=bucket_name)
    assert_status_code(resp, 204)


@pytest.fixture
async def bucket_name(region, create_bucket):
    name = await create_bucket(region)
    yield name


@pytest.fixture
async def table_name(create_table):
    name = await create_table()
    yield name


@pytest.fixture
async def create_bucket(s3_client):
    _bucket_name = None

    async def _f(region_name, bucket_name=None):
        nonlocal _bucket_name
        if bucket_name is None:
            bucket_name = random_bucketname()
        _bucket_name = bucket_name
        bucket_kwargs = {'Bucket': bucket_name}
        if region_name != 'us-east-1':
            bucket_kwargs['CreateBucketConfiguration'] = {
                'LocationConstraint': region_name,
            }
        response = await s3_client.create_bucket(**bucket_kwargs)
        assert_status_code(response, 200)
        await s3_client.put_bucket_versioning(
            Bucket=bucket_name, VersioningConfiguration={'Status': 'Enabled'})
        return bucket_name

    try:
        yield _f
    finally:
        await recursive_delete(s3_client, _bucket_name)


@pytest.fixture
async def create_table(dynamodb_client):
    _table_name = None

    async def _is_table_ready(table_name):
        response = await dynamodb_client.describe_table(
            TableName=table_name
        )
        return response['Table']['TableStatus'] == 'ACTIVE'

    async def _f(table_name=None):
        nonlocal _table_name
        if table_name is None:
            table_name = random_tablename()
        _table_name = table_name
        table_kwargs = {
            'TableName': table_name,
            'AttributeDefinitions': [
                {
                    'AttributeName': 'testKey',
                    'AttributeType': 'S'
                },
            ],
            'KeySchema': [
                {
                    'AttributeName': 'testKey',
                    'KeyType': 'HASH'
                },
            ],
            'ProvisionedThroughput': {
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            },
        }

        response = await dynamodb_client.create_table(**table_kwargs)
        while not (await _is_table_ready(table_name)):
            pass

        assert_status_code(response, 200)
        return table_name

    try:
        yield _f
    finally:
        await delete_table(dynamodb_client, _table_name)


async def delete_table(dynamodb_client, table_name):
    response = await dynamodb_client.delete_table(
        TableName=table_name
    )
    assert_status_code(response, 200)


@pytest.fixture
def tempdir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def create_object(s3_client, bucket_name):
    async def _f(key_name, body='foo'):
        r = await s3_client.put_object(Bucket=bucket_name, Key=key_name,
                                       Body=body)
        assert_status_code(r, 200)
        return r
    return _f


@pytest.fixture
def create_multipart_upload(request, s3_client, bucket_name, event_loop):
    _key_name = None
    upload_id = None

    async def _f(key_name):
        nonlocal _key_name
        nonlocal upload_id
        _key_name = key_name

        parsed = await s3_client.create_multipart_upload(
            Bucket=bucket_name, Key=key_name)
        upload_id = parsed['UploadId']
        return upload_id

    def fin():
        event_loop.run_until_complete(s3_client.abort_multipart_upload(
            UploadId=upload_id, Bucket=bucket_name, Key=_key_name))

    request.addfinalizer(fin)
    return _f


@pytest.fixture
async def aio_session():
    async with aiohttp.ClientSession() as session:
        yield session


def pytest_configure():
    class AIOUtils:
        def __init__(self):
            self.assert_status_code = assert_status_code
            self.assert_num_uploads_found = assert_num_uploads_found

    pytest.aio = AIOUtils()


@pytest.fixture
def dynamodb_put_item(dynamodb_client, table_name):

    async def _f(key_string_value):
        response = await dynamodb_client.put_item(
            TableName=table_name,
            Item={
                'testKey': {
                    'S': key_string_value
                }
            },
        )
        assert_status_code(response, 200)

    return _f


@pytest.fixture
def topic_arn(region, create_topic, sns_client, event_loop):
    arn = event_loop.run_until_complete(create_topic())
    return arn


async def delete_topic(sns_client, topic_arn):
    response = await sns_client.delete_topic(
        TopicArn=topic_arn
    )
    assert_status_code(response, 200)


@pytest.fixture
def create_topic(request, sns_client, event_loop):
    _topic_arn = None

    async def _f():
        nonlocal _topic_arn
        response = await sns_client.create_topic(Name=random_name())
        _topic_arn = response['TopicArn']
        assert_status_code(response, 200)
        return _topic_arn

    def fin():
        event_loop.run_until_complete(delete_topic(sns_client, _topic_arn))

    request.addfinalizer(fin)
    return _f


@pytest.fixture
async def sqs_queue_url(sqs_client):
    response = await sqs_client.create_queue(QueueName=random_name())
    queue_url = response['QueueUrl']
    assert_status_code(response, 200)

    try:
        yield queue_url
    finally:
        await delete_sqs_queue(sqs_client, queue_url)


async def delete_sqs_queue(sqs_client, queue_url):
    response = await sqs_client.delete_queue(
        QueueUrl=queue_url
    )
    assert_status_code(response, 200)


pytest_plugins = ['mock_server']
