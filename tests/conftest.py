import asyncio
import pytest
import time
import aiohttp
import aiobotocore
from aiobotocore.client import AioConfig
import tempfile
import shutil


@pytest.fixture
def loop(request):
    old_loop = asyncio.get_event_loop()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(None)

    def fin():
        loop.close()
        asyncio.set_event_loop(old_loop)

    request.addfinalizer(fin)
    return loop


@pytest.mark.tryfirst
def pytest_pycollect_makeitem(collector, name, obj):
    if collector.funcnamefilter(name):
        item = pytest.Function(name, parent=collector)
        if 'run_loop' in item.keywords:
            return list(collector._genfunctions(name, obj))


@pytest.mark.tryfirst
def pytest_pyfunc_call(pyfuncitem):
    """
    Run asyncio marked test functions in an event loop instead of a normal
    function call.
    """
    if 'run_loop' in pyfuncitem.keywords:
        funcargs = pyfuncitem.funcargs
        loop = funcargs['loop']
        testargs = {arg: funcargs[arg]
                    for arg in pyfuncitem._fixtureinfo.argnames}

        if not asyncio.iscoroutinefunction(pyfuncitem.obj):
            func = asyncio.coroutine(pyfuncitem.obj)
        else:
            func = pyfuncitem.obj
        loop.run_until_complete(func(**testargs))
        return True


def pytest_runtest_setup(item):
    if 'run_loop' in item.keywords and 'loop' not in item.fixturenames:
        # inject an event loop fixture for all async tests
        item.fixturenames.append('loop')


def random_bucketname():
    # 63 is the max bucket length.
    return random_name()


def random_tablename():
    return random_name()


def random_name():
    table_name = 'aiobotocoretest_{}'
    t = time.time()
    return table_name.format(int(t))


def assert_status_code(response, status_code):
    assert response['ResponseMetadata']['HTTPStatusCode'] == status_code


@asyncio.coroutine
def assert_num_uploads_found(s3_client, bucket_name, operation,
                             num_uploads, *, max_items=None, num_attempts=5,
                             loop):
    amount_seen = None
    paginator = s3_client.get_paginator(operation)
    for _ in range(num_attempts):
        pages = paginator.paginate(Bucket=bucket_name,
                                   PaginationConfig={'MaxItems': max_items})
        responses = []
        while True:
            resp = yield from pages.next_page()
            if resp is None:
                break
            responses.append(resp)
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
            yield from asyncio.sleep(2, loop=loop)
        pytest.fail("Expected to see %s uploads, instead saw: %s" % (
            num_uploads, amount_seen))


@pytest.fixture
def session(loop):
    session = aiobotocore.get_session(loop=loop)
    return session


@pytest.fixture
def region():
    return 'us-east-1'


@pytest.fixture
def signature_version():
    return 's3'


@pytest.fixture
def config(region, signature_version):
    return AioConfig(region_name=region, signature_version=signature_version)


@pytest.fixture
def s3_client(request, session, region, config):
    client = create_client('s3', request, session, region, config)
    return client


@pytest.fixture
def dynamodb_client(request, session, region, config):
    client = create_client('dynamodb', request, session, region, config)
    return client


def create_client(client_type, request, session, region, config):
    client = session.create_client(client_type, region_name=region,
                                   config=config)

    def fin():
        client.close()
    request.addfinalizer(fin)
    return client


@asyncio.coroutine
def recursive_delete(s3_client, bucket_name):
    # Recursively deletes a bucket and all of its contents.
    pages = s3_client.get_paginator('list_objects').paginate(
        Bucket=bucket_name)

    while True:
        n = yield from pages.next_page()
        if n is None:
            break

        if 'Contents' not in n:
            continue

        for obj in n['Contents']:
            key = obj['Key']
            if key is None:
                continue
            yield from s3_client.delete_object(Bucket=bucket_name, Key=key)

    resp = yield from s3_client.delete_bucket(Bucket=bucket_name)
    assert_status_code(resp, 204)


@pytest.fixture
def bucket_name(region, create_bucket, s3_client, loop):
    name = loop.run_until_complete(create_bucket(region))
    return name


@pytest.fixture
def table_name(region, create_table, dynamodb_client, loop):
    name = loop.run_until_complete(create_table())
    return name


@pytest.fixture
def create_bucket(request, s3_client, loop):
    _bucket_name = None

    @asyncio.coroutine
    def _f(region_name, bucket_name=None):

        nonlocal _bucket_name
        if bucket_name is None:
            bucket_name = random_bucketname()
        _bucket_name = bucket_name
        bucket_kwargs = {'Bucket': bucket_name}
        if region_name != 'us-east-1':
            bucket_kwargs['CreateBucketConfiguration'] = {
                'LocationConstraint': region_name,
            }
        response = yield from s3_client.create_bucket(**bucket_kwargs)
        assert_status_code(response, 200)
        return bucket_name

    def fin():
        loop.run_until_complete(recursive_delete(s3_client, _bucket_name))

    request.addfinalizer(fin)
    return _f


@pytest.fixture
def create_table(request, dynamodb_client, loop):
    _table_name = None

    @asyncio.coroutine
    def _is_table_ready(table_name):
        response = yield from dynamodb_client.describe_table(
            TableName=table_name
        )
        return response['Table']['TableStatus'] == 'ACTIVE'

    @asyncio.coroutine
    def _f(table_name=None):

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
        response = yield from dynamodb_client.create_table(**table_kwargs)
        while not (yield from _is_table_ready(table_name)):
            pass
        assert_status_code(response, 200)
        return table_name

    def fin():
        loop.run_until_complete(delete_table(dynamodb_client, _table_name))

    request.addfinalizer(fin)
    return _f


@asyncio.coroutine
def delete_table(dynamodb_client, table_name):
    response = yield from dynamodb_client.delete_table(
        TableName=table_name
    )
    assert_status_code(response, 200)


@pytest.fixture
def tempdir(request):
    tempdir = tempfile.mkdtemp()

    def fin():
        shutil.rmtree(tempdir)
    request.addfinalizer(fin)
    return tempdir


@pytest.fixture
def create_object(s3_client, bucket_name):

    @asyncio.coroutine
    def _f(key_name, body='foo'):
        r = yield from s3_client.put_object(Bucket=bucket_name, Key=key_name,
                                            Body=body)
        assert_status_code(r, 200)
        return r
    return _f


@pytest.fixture
def create_multipart_upload(request, s3_client, bucket_name, loop):
    _key_name = None
    upload_id = None

    @asyncio.coroutine
    def _f(key_name):
        nonlocal _key_name
        nonlocal upload_id
        _key_name = key_name

        parsed = yield from s3_client.create_multipart_upload(
            Bucket=bucket_name, Key=key_name)
        upload_id = parsed['UploadId']
        return upload_id

    def fin():
        loop.run_until_complete(s3_client.abort_multipart_upload(
            UploadId=upload_id, Bucket=bucket_name, Key=_key_name))

    request.addfinalizer(fin)
    return _f


@pytest.yield_fixture
def aio_session(request, loop):
    session = aiohttp.ClientSession(loop=loop)
    yield session
    loop.run_until_complete(session.close())


def pytest_namespace():
    return {'aio': {'assert_status_code': assert_status_code,
                    'assert_num_uploads_found': assert_num_uploads_found},
            }


@pytest.fixture
def dynamodb_put_item(request, dynamodb_client, table_name, loop):

    @asyncio.coroutine
    def _f(key_string_value):
        response = yield from dynamodb_client.put_item(
            TableName=table_name,
            Item={
                'testKey': {
                    'S': key_string_value
                }
            },
        )
        assert_status_code(response, 200)

    return _f
