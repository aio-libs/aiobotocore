import pytest

from tests.mock_server import AIOServer


async def test_serves_once_entered():
    async with AIOServer() as server:
        assert server.endpoint_url.startswith('http://127.0.0.1:')


async def _never_ready(*args, **kwargs):
    return False


async def test_reports_a_thread_that_never_becomes_ready(monkeypatch):
    # Nothing sets _ready, so entering must give up rather than serve nothing.
    server = AIOServer()
    monkeypatch.setattr(server, '_run', lambda: None)
    monkeypatch.setattr(server, '_wait_until_up', _never_ready)

    with pytest.raises(pytest.fail.Exception):
        await server.__aenter__()


async def test_reraises_a_failure_from_the_server_thread(monkeypatch):
    server = AIOServer()

    def boom():
        server._error = ValueError('not a connection problem')
        server._ready.set()

    monkeypatch.setattr(server, '_run', boom)

    with pytest.raises(ValueError, match='not a connection problem'):
        await server.__aenter__()


async def test_reports_a_thread_that_exits_before_binding(monkeypatch):
    # Ready, but no endpoint: the loop died between starting and binding.
    server = AIOServer()
    monkeypatch.setattr(server, '_run', server._ready.set)

    with pytest.raises(pytest.fail.Exception):
        await server.__aenter__()


async def test_shutdown_reaps_the_thread():
    server = AIOServer()
    async with server:
        assert server._thread.is_alive()

    assert not server._thread.is_alive()
