import asyncio
from unittest import mock

from aiobotocore import credentials


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

    # A mock provider whose load() yields control via asyncio.sleep(0),
    # allowing another task to interleave and expose the race condition.
    static_creds = credentials.AioCredentials('akid', 'skid')

    class _YieldingProvider:
        METHOD = 'mock-static'
        CANONICAL_NAME = None

        async def load(self):
            await asyncio.sleep(0)
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
    results = await asyncio.gather(provider.load(), provider.load())
    assert all(r is not None for r in results)
