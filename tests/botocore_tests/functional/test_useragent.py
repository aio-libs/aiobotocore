import anyio
import pytest

from aiobotocore.session import AioSession

from ...mock_server import AIOServer
from .. import ClientHTTPStubber

pytestmark = pytest.mark.anyio


def get_captured_ua_strings(stubber):
    """Get captured request-level user agent strings from stubber.
    :type stubber: tests.BaseHTTPStubber
    """
    return [req.headers['User-Agent'].decode() for req in stubber.requests]


def parse_registered_feature_ids(ua_string):
    """Parse registered feature ids in user agent string.
    :type ua_string: str
    :rtype: list[str]
    """
    ua_fields = ua_string.split(' ')
    feature_field = [field for field in ua_fields if field.startswith('m/')][0]
    return feature_field[2:].split(',')


async def test_user_agent_has_registered_feature_id():
    session = AioSession()

    async with (
        AIOServer() as server,
        session.create_client(
            's3',
            endpoint_url=server.endpoint_url,
            aws_secret_access_key='xxx',
            aws_access_key_id='xxx',
        ) as s3_client,
    ):
        with ClientHTTPStubber(s3_client) as stub_client:
            stub_client.add_response()
            paginator = s3_client.get_paginator('list_buckets')
            # The `paginate()` method registers `'PAGINATOR': 'C'`
            async for _ in paginator.paginate():
                pass

        ua_string = get_captured_ua_strings(stub_client)[0]
        feature_list = parse_registered_feature_ids(ua_string)
        assert 'C' in feature_list


async def test_registered_feature_ids_dont_bleed_between_requests():
    session = AioSession()

    async with (
        AIOServer() as server,
        session.create_client(
            's3',
            endpoint_url=server.endpoint_url,
            aws_secret_access_key='xxx',
            aws_access_key_id='xxx',
        ) as s3_client,
    ):
        with ClientHTTPStubber(s3_client) as stub_client:
            stub_client.add_response()
            waiter = s3_client.get_waiter('bucket_exists')
            # The `wait()` method registers `'WAITER': 'B'`
            await waiter.wait(Bucket='mybucket')

            stub_client.add_response()
            paginator = s3_client.get_paginator('list_buckets')
            # The `paginate()` method registers `'PAGINATOR': 'C'`
            async for _ in paginator.paginate():
                pass

        ua_strings = get_captured_ua_strings(stub_client)
        waiter_feature_list = parse_registered_feature_ids(ua_strings[0])
        assert 'B' in waiter_feature_list

        paginator_feature_list = parse_registered_feature_ids(ua_strings[1])
        assert 'C' in paginator_feature_list
        assert 'B' not in paginator_feature_list


# This tests context's bleeding across tasks instead
async def test_registered_feature_ids_dont_bleed_across_threads():
    session = AioSession()

    async with (
        AIOServer() as server,
        session.create_client(
            's3',
            endpoint_url=server.endpoint_url,
            aws_secret_access_key='xxx',
            aws_access_key_id='xxx',
        ) as s3_client,
    ):
        waiter_features = []
        paginator_features = []

        async def wait():
            with ClientHTTPStubber(s3_client) as stub_client:
                stub_client.add_response()
                waiter = s3_client.get_waiter('bucket_exists')
                # The `wait()` method registers `'WAITER': 'B'`
                await waiter.wait(Bucket='mybucket')
            ua_string = get_captured_ua_strings(stub_client)[0]
            waiter_features.extend(parse_registered_feature_ids(ua_string))

        async def paginate():
            with ClientHTTPStubber(s3_client) as stub_client:
                stub_client.add_response()
                paginator = s3_client.get_paginator('list_buckets')
                # The `paginate()` method registers `'PAGINATOR': 'C'`
                async for _ in paginator.paginate():
                    pass
            ua_string = get_captured_ua_strings(stub_client)[0]
            paginator_features.extend(parse_registered_feature_ids(ua_string))

        async with anyio.create_task_group() as tg:
            tg.start_soon(wait)
            tg.start_soon(paginate)

        assert 'B' in waiter_features
        assert 'C' not in waiter_features
        assert 'C' in paginator_features
        assert 'B' not in paginator_features
