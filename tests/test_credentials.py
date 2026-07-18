from unittest import mock

import anyio
import pytest

from aiobotocore import credentials, utils


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
        credentials.AnyioContainerProvider()._fetcher,
        utils.AnyioContainerMetadataFetcher,
    )
    assert isinstance(
        credentials.AioContainerProvider()._fetcher,
        utils.AioContainerMetadataFetcher,
    )
