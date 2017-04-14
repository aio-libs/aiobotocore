import asyncio
import pytest
import time
import aiohttp
import aiobotocore
from aiobotocore.config import AioConfig
import tempfile
import shutil


@pytest.fixture(scope="session", params=[True, False],
                ids=['debug[true]', 'debug[false]'])
def debug(request):
    return request.param


@pytest.yield_fixture
def loop(request, debug):
    try:
        old_loop = asyncio.get_event_loop()
    except RuntimeError:
        old_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(None)
    loop.set_debug(debug)

    yield loop

    loop.close()
    asyncio.set_event_loop(old_loop)


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
    """Return a string with presumably unique contents

    The string contains only symbols allowed for s3 buckets
    (alpfanumeric, dot and hyphen).
    """
    table_name = 'aiobotocoretest-{}'
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
def aa_fail_proxy_config(monkeypatch):
    # NOTE: name of this fixture must be alphabetically first to run first
    monkeypatch.setenv('HTTP_PROXY', 'http://localhost:54321')
    monkeypatch.setenv('HTTPS_PROXY', 'http://localhost:54321')


@pytest.fixture
def aa_succeed_proxy_config(monkeypatch):
    # NOTE: name of this fixture must be alphabetically first to run first
    monkeypatch.setenv('HTTP_PROXY', 'http://localhost:54321')
    monkeypatch.setenv('HTTPS_PROXY', 'http://localhost:54321')

    # this will cause us to skip proxying
    monkeypatch.setenv('NO_PROXY', 'amazonaws.com')


@pytest.fixture
def session(loop):
    session = aiobotocore.get_session(loop=loop)
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
    return AioConfig(region_name=region, signature_version=signature_version,
                     read_timeout=5, connect_timeout=5)


@pytest.fixture
def mocking_test():
    # change this flag for test with real aws
    # TODO: this should be merged with pytest.mark.moto
    return True


def moto_config(endpoint_url):
    AWS_ACCESS_KEY_ID = "xxx"
    AWS_SECRET_ACCESS_KEY = "xxx"
    kw = dict(endpoint_url=endpoint_url,
              aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
              aws_access_key_id=AWS_ACCESS_KEY_ID)
    return kw


@pytest.fixture
def s3_client(request, session, region, config, s3_server, mocking_test, loop):
    kw = {}
    if mocking_test:
        kw = moto_config(s3_server)
    client = create_client('s3', request, loop, session, region, config, **kw)
    return client


@pytest.fixture
def alternative_s3_client(request, session, alternative_region,
                          signature_version, s3_server,
                          mocking_test, loop):
    kw = {}
    if mocking_test:
        kw = moto_config(s3_server)

    config = AioConfig(
        region_name=alternative_region, signature_version=signature_version,
        read_timeout=5, connect_timeout=5)
    client = create_client(
        's3', request, loop, session, alternative_region, config, **kw)
    return client


@pytest.fixture
def dynamodb_client(request, session, region, config, dynamodb2_server,
                    mocking_test, loop):
    kw = {}
    if mocking_test:
        kw = moto_config(dynamodb2_server)
    client = create_client('dynamodb', request, loop, session, region,
                           config, **kw)
    return client


@pytest.fixture
def cloudformation_client(request, session, region, config,
                          cloudformation_server, mocking_test, loop):
    kw = {}
    if mocking_test:
        kw = moto_config(cloudformation_server)
    client = create_client('cloudformation', request, loop, session, region,
                           config, **kw)
    return client


def create_client(client_type, request, loop, session, region, config, **kw):
    @asyncio.coroutine
    def f():
        return session.create_client(client_type, region_name=region,
                                     config=config, **kw)
    client = loop.run_until_complete(f())

    def fin():
        loop.run_until_complete(client.close())
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

    @asyncio.coroutine
    def create_session(loop):
        return aiohttp.ClientSession(loop=loop)

    session = loop.run_until_complete(create_session(loop))
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


pytest_plugins = ['mock_server']
