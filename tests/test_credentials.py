from unittest import mock

import anyio
import pytest

from aiobotocore import credentials, utils
from aiobotocore.httpxsession import is_httpx_session_cls


async def test_refreshable_credentials_serialize_refreshes(http_session_cls):
    credential_cls = (
        credentials.AnyioRefreshableCredentials
        if is_httpx_session_cls(http_session_cls)
        else credentials.AioRefreshableCredentials
    )
    refresh_calls = 0

    async def refresh():
        nonlocal refresh_calls
        refresh_calls += 1
        await anyio.sleep(0)
        return {
            'access_key': 'refreshed-access',
            'secret_key': 'refreshed-secret',
            'token': 'refreshed-token',
            'expiry_time': '2030-01-01T00:00:00Z',
        }

    creds = credential_cls.create_from_metadata(
        metadata={
            'access_key': 'expired-access',
            'secret_key': 'expired-secret',
            'token': 'expired-token',
            'expiry_time': '2000-01-01T00:00:00Z',
        },
        refresh_using=refresh,
        method='test',
    )
    results = []

    async def get_credentials():
        results.append(await creds.get_frozen_credentials())

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(get_credentials)
        task_group.start_soon(get_credentials)

    assert refresh_calls == 1
    assert [result.access_key for result in results] == [
        'refreshed-access',
        'refreshed-access',
    ]


async def test_assumerolecredprovider_concurrent_load_no_race_condition():
    """Regression test for https://github.com/aio-libs/aiobotocore/issues/1455.

    When multiple async tasks share the same AioAssumeRoleProvider and call
    load() concurrently, _visited_profiles must not leak between tasks.
    Without the fix, a second task entering load() while the first task is
    awaiting inside _resolve_credentials_from_profile would see the first
    task's _visited_profiles entries and raise InfiniteLoopConfigError.
    """
    fake_config = {
        'profiles': {
            'a': {
                'role_arn': 'arn:aws:iam::123456789012:role/RoleA',
                'source_profile': 'b',
            },
            'b': {
                'aws_access_key_id': 'akid',
                'aws_secret_access_key': 'skid',
            },
        }
    }

    # A mock provider whose load() yields control, allowing another task to
    # interleave and expose the race condition.
    static_creds = credentials.AioCredentials('akid', 'skid')

    class _YieldingProvider:
        METHOD = 'mock-static'
        CANONICAL_NAME = None

        async def load(self):
            await anyio.sleep(0)
            return static_creds

    mock_builder = mock.Mock()
    mock_builder.providers.return_value = [_YieldingProvider()]

    # client_creator is never invoked: load() returns AioDeferredRefreshableCredentials
    # without calling STS, so a bare Mock() is sufficient.
    provider = credentials.AioAssumeRoleProvider(
        lambda: fake_config,
        mock.Mock(),
        cache={},
        profile_name='a',
        profile_provider_builder=mock_builder,
    )

    # Both tasks must succeed; without the fix the second task raises
    # InfiniteLoopConfigError because it sees 'b' already in _visited_profiles.
    results = [None, None]

    async def load_into(index):
        results[index] = await provider.load()

    async with anyio.create_task_group() as tg:
        tg.start_soon(load_into, 0)
        tg.start_soon(load_into, 1)

    assert all(r is not None for r in results)


@pytest.fixture
def container_fetcher_cls(current_http_backend):
    # aiohttp sleeps via asyncio, httpx (which also runs on trio) via anyio.
    if current_http_backend == 'httpx':
        return utils.AnyioContainerMetadataFetcher
    return utils.AioContainerMetadataFetcher


async def test_container_provider_keeps_a_caller_supplied_fetcher(
    container_fetcher_cls,
):
    # botocore's blocking default is swapped for an async one, but a fetcher
    # the caller passed in (with its own session or timeout) is left alone.
    fetcher = container_fetcher_cls()
    provider = credentials.AnyioContainerProvider(fetcher=fetcher)
    assert provider._fetcher is fetcher

    assert isinstance(
        credentials.AioContainerProvider()._fetcher,
        utils.AioContainerMetadataFetcher,
    )


def test_anyio_container_provider_uses_httpx_fetcher_by_default():
    # The anyio provider's default fetcher is the httpx-backed
    # AnyioContainerMetadataFetcher, whose construction requires httpx.
    pytest.importorskip("httpx")
    assert isinstance(
        credentials.AnyioContainerProvider()._fetcher,
        utils.AnyioContainerMetadataFetcher,
    )
