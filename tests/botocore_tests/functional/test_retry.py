"""Taken from https://github.com/boto/botocore/blob/develop/tests/functional/test_retry.py
and adapted for asyncio and pytest.

``BaseRetryTest`` becomes module-level helpers: ``assert_will_retry_n_times``
is an async context manager and the endpoint sleep is patched by an autouse
fixture, since aiobotocore sleeps through ``AioEndpoint._sleep`` rather than
``time.sleep``.
"""

import contextlib
import json

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from .. import ClientHTTPStubber
from .test_useragent import (
    get_captured_ua_strings,
    parse_registered_feature_ids,
)

REGION = 'us-west-2'


@pytest.fixture(autouse=True)
def patched_sleep(mocker):
    mocker.patch(
        'aiobotocore.endpoint.AioEndpoint._sleep',
        new_callable=mocker.AsyncMock,
    )
    mocker.patch(
        'aiobotocore.endpoint.AnyioEndpoint._sleep',
        new_callable=mocker.AsyncMock,
    )


@pytest.fixture
def new_retries_enabled(mocker):
    # aiobotocore's register_retry_handler reads its own imported copy.
    mocker.patch('botocore.retries.standard.NEW_RETRIES_ENABLED', True)
    mocker.patch('aiobotocore.retries.standard.NEW_RETRIES_ENABLED', True)


@contextlib.asynccontextmanager
async def assert_will_retry_n_times(
    client, num_retries, status=500, body=b'{}'
):
    num_responses = num_retries + 1
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    with ClientHTTPStubber(client) as http_stubber:
        for _ in range(num_responses):
            http_stubber.add_response(status=status, body=body)
        with pytest.raises(
            ClientError, match=f'reached max retries: {num_retries}'
        ):
            yield
        assert len(http_stubber.requests) == num_responses


async def get_feature_id_lists_from_retries(client):
    with ClientHTTPStubber(client) as http_stubber:
        # Add two failed responses followed by a success
        http_stubber.add_response(status=502, body=b'{}')
        http_stubber.add_response(status=502, body=b'{}')
        http_stubber.add_response(status=200, body=b'{}')
        await client.list_tables()
    ua_strings = get_captured_ua_strings(http_stubber)
    return [
        parse_registered_feature_ids(ua_string) for ua_string in ua_strings
    ]


async def sdk_request_headers(session, config, responses):
    async with session.create_client(
        'dynamodb', REGION, config=config
    ) as client:
        with ClientHTTPStubber(client) as http_stubber:
            for status in responses:
                http_stubber.add_response(status=status, body=b'{}')
            await client.list_tables()
            return [
                r.headers['amz-sdk-request'] for r in http_stubber.requests
            ]


async def test_can_override_max_attempts(patched_session):
    async with patched_session.create_client(
        'dynamodb', REGION, config=Config(retries={'max_attempts': 1})
    ) as client:
        async with assert_will_retry_n_times(client, 1):
            await client.list_tables()


async def test_standard_mode_has_default_3_retries(patched_session):
    async with patched_session.create_client(
        'dynamodb', REGION, config=Config(retries={'mode': 'standard'})
    ) as client:
        async with assert_will_retry_n_times(client, 2):
            await client.list_tables()


async def test_user_agent_has_legacy_mode_feature_id(patched_session):
    async with patched_session.create_client('dynamodb', REGION) as client:
        feature_lists = await get_feature_id_lists_from_retries(client)
    # Confirm all requests register `'RETRY_MODE_LEGACY': 'D'`
    assert all('D' in feature_list for feature_list in feature_lists)


@pytest.mark.parametrize('retry_mode', ['standard', 'adaptive'])
async def test_max_present_on_initial_attempt(
    patched_session, new_retries_enabled, retry_mode
):
    config = Config(retries={'mode': retry_mode, 'total_max_attempts': 3})
    headers = await sdk_request_headers(patched_session, config, [200])
    assert headers == [b'attempt=1; max=3']


@pytest.mark.parametrize('retry_mode', ['standard', 'adaptive'])
async def test_max_consistent_across_retries(
    patched_session, new_retries_enabled, retry_mode
):
    config = Config(retries={'mode': retry_mode, 'total_max_attempts': 3})
    headers = await sdk_request_headers(
        patched_session, config, [500, 500, 200]
    )
    assert len(headers) == 3
    assert all(b'max=3' in header for header in headers)


async def test_service_specific_max_attempts(
    patched_session, new_retries_enabled
):
    # DynamoDB overrides the default of 3.
    config = Config(retries={'mode': 'standard'})
    headers = await sdk_request_headers(patched_session, config, [200])
    assert headers == [b'attempt=1; max=4']
