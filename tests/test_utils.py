from unittest import mock

import anyio
import pytest

from aiobotocore import utils
from aiobotocore.httpxsession import is_httpx_session_cls


async def test_s3express_cache_serializes_concurrent_refreshes(
    http_session_cls,
):
    client = mock.Mock()
    client.create_session = mock.AsyncMock(
        return_value={
            'Credentials': {
                'AccessKeyId': 'access',
                'SecretAccessKey': 'secret',
                'SessionToken': 'token',
                'Expiration': '2030-01-01T00:00:00Z',
            }
        }
    )
    credential = object()
    credential_cls = mock.Mock()
    credential_cls.create_from_metadata.return_value = credential
    cache_cls = (
        utils.AnyioS3ExpressIdentityCache
        if is_httpx_session_cls(http_session_cls)
        else utils.AioS3ExpressIdentityCache
    )
    cache = cache_cls(client, credential_cls)
    results = []

    async def get_credentials():
        results.append(await cache.get_credentials('bucket'))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(get_credentials)
        task_group.start_soon(get_credentials)

    assert results == [credential, credential]

    for index in range(1, 100):
        bucket = f'bucket-{index}'
        cache._credentials[bucket] = object()
        cache._refresh_locks[bucket] = object()

    await cache.get_credentials('new-bucket')

    assert 'bucket' not in cache._credentials
    assert 'bucket' not in cache._refresh_locks
    assert 'new-bucket' in cache._credentials
    assert client.create_session.await_args_list == [
        mock.call(Bucket='bucket'),
        mock.call(Bucket='new-bucket'),
    ]


async def test_ref_counted_session_rolls_back_the_count_when_entry_fails_httpx():
    pytest.importorskip("httpx")

    class _Failing(utils._RefCountedHttpxSession):
        async def __aenter__(self):
            raise RuntimeError('no session for you')

    session = _Failing()

    with pytest.raises(RuntimeError, match='no session for you'):
        async with session.acquire():
            pass  # pragma: no cover

    # The second acquire must try __aenter__ again: if the failed first one
    # left the ref count raised, acquire would take the count > 1 branch and
    # hand out a session that was never entered.
    with pytest.raises(RuntimeError, match='no session for you'):
        async with session.acquire():
            pass  # pragma: no cover


async def test_ref_counted_session_rolls_back_the_count_when_entry_fails():
    class _Failing(utils._RefCountedSession):
        async def __aenter__(self):
            raise RuntimeError('no session for you')

    session = _Failing()

    with pytest.raises(RuntimeError, match='no session for you'):
        async with session.acquire():
            pass  # pragma: no cover

    # The second acquire must try __aenter__ again: if the failed first one
    # left the ref count raised, acquire would take the count > 1 branch and
    # hand out a session that was never entered.
    with pytest.raises(RuntimeError, match='no session for you'):
        async with session.acquire():
            pass  # pragma: no cover
