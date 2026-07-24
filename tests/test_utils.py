import pytest

from aiobotocore import utils


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
