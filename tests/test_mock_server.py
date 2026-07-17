import anyio
import pytest

from tests.mock_server import AIOServer


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the retry backoff, which is 30 * 0.5s in the failure cases."""

    async def _no_sleep(delay):
        pass

    monkeypatch.setattr(anyio, 'sleep', _no_sleep)


async def test_wait_until_up_retries_until_the_server_answers(
    monkeypatch, no_sleep
):
    # A connect succeeding only means the socket is listening, and a listening
    # socket answers before run_app can serve, so a non-200 has to keep waiting.
    replies = [
        OSError('connection refused'),
        b'HTTP/1.1 503 Service Unavailable\r\n\r\n',
        b'HTTP/1.1 200 OK\r\n\r\n',
    ]

    async def scripted_get_ok():
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    server = AIOServer()
    monkeypatch.setattr(server, '_get_ok', scripted_get_ok)

    await server._wait_until_up()

    assert replies == []


async def test_wait_until_up_fails_when_the_server_never_answers(
    monkeypatch, no_sleep
):
    async def refused():
        raise OSError('connection refused')

    server = AIOServer()
    monkeypatch.setattr(server, '_get_ok', refused)

    with pytest.raises(pytest.fail.Exception):
        await server._wait_until_up()


async def test_wait_until_up_fails_on_an_unexpected_error(monkeypatch):
    async def boom():
        raise ValueError('not a connection problem')

    server = AIOServer()
    monkeypatch.setattr(server, '_get_ok', boom)

    with pytest.raises(pytest.fail.Exception):
        await server._wait_until_up()


async def test_wait_until_up_fails_when_the_server_process_died():
    class _Dead(AIOServer):
        exitcode = 1

    with pytest.raises(pytest.fail.Exception):
        await _Dead()._wait_until_up()
