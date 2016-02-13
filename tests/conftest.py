import asyncio
import pytest
import time
import aiobotocore
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
    bucket_name = 'aiobotocoretest_{}'
    t = time.time()
    return bucket_name.format(int(t))


def assert_status_code(response, status_code):
    assert response['ResponseMetadata']['HTTPStatusCode'] == status_code


@pytest.fixture
def session(loop):
    session = aiobotocore.get_session(loop=loop)
    return session


@pytest.fixture
def region():
    return 'us-east-1'


@pytest.fixture
def s3_client(request, session, region):
    region = 'us-east-1'
    client = session.create_client('s3', region_name=region)

    def fin():
        client.close()
    request.addfinalizer(fin)
    return client


@pytest.fixture
def bucket_name(region):
    return 'dataintake-test'


@pytest.fixture
def create_bucket(request, s3_client, loop):
    _bucket_name = None

    @asyncio.coroutine
    def _f(region_name, bucket_name=None):
        return 'dataintake-test'

        nonlocal _bucket_name
        bucket_kwargs = {}
        if bucket_name is None:
            bucket_name = random_bucketname()
        _bucket_name = bucket_name
        bucket_kwargs = {'Bucket': bucket_name}
        if region_name != 'us-east-1':
            bucket_kwargs['CreateBucketConfiguration'] = {
                'LocationConstraint': region_name,
            }
        response = s3_client.create_bucket(**bucket_kwargs)
        assert_status_code(response, 200)
        return 'dataintake-test'
        # return bucket_name

    def fin():
        resp = loop.run_unti_complete(
            s3_client.delete_bucket(Bucket=_bucket_name))
        assert_status_code(resp, 200)

    # request.addfinalizer(fin)
    return _f


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
        return r
    return _f


def pytest_namespace():
    return {'aio': {'assert_status_code': assert_status_code}}
