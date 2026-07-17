import asyncio
import multiprocessing
import socket

# Third Party
import aiohttp
import aiohttp.web
import anyio
import pytest
from aiohttp.web import StreamResponse
from moto.server import ThreadedMotoServer

_proxy_bypass = {
    "http": None,
    "https": None,
}

host = '127.0.0.1'


def get_free_tcp_port(release_socket: bool = False):
    sckt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sckt.bind((host, 0))
    addr, port = sckt.getsockname()
    if release_socket:
        sckt.close()
        return port

    return sckt, port


# This runs in a subprocess for a variety of reasons
# 1) early versions of python 3.5 did not correctly set one thread per run loop
# 2) aiohttp uses get_event_loop instead of using the passed in run loop
# 3) aiohttp shutdown can be hairy
class AIOServer(multiprocessing.Process):
    """
    This is a mock AWS service which will 5 seconds before returning
    a response to test socket timeouts.
    """

    def __init__(self):
        super().__init__(target=self._run)
        self._loop = None
        self._port = get_free_tcp_port(True)
        self.endpoint_url = f'http://{host}:{self._port}'
        self.daemon = True  # die when parent dies

    def _run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        app = aiohttp.web.Application()
        app.router.add_route('*', '/ok', self.ok)
        app.router.add_route('*', '/{anything:.*}', self.stream_handler)

        try:
            aiohttp.web.run_app(
                app, host=host, port=self._port, handle_signals=False
            )
        except BaseException:
            pytest.fail('unable to start and connect to aiohttp server')
            raise

    async def __aenter__(self):
        self.start()
        await self._wait_until_up()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            self.terminate()
        except BaseException:
            pytest.fail("Unable to shut down server")
            raise

    @staticmethod
    async def ok(request):
        return aiohttp.web.Response()

    async def stream_handler(self, request):
        # Without the Content-Type, most (all?) browsers will not render
        # partially downloaded content. Note, the response type is
        # StreamResponse not Response.
        resp = StreamResponse(
            status=200, reason='OK', headers={'Content-Type': 'text/html'}
        )

        await resp.prepare(request)
        await asyncio.sleep(5)
        await resp.drain()
        return resp

    async def _get_ok(self):
        """GET /ok over a raw stream, returning the first bytes of the reply.

        Runs on the test's framework, which is trio for the httpx backend, so
        it cannot use aiohttp or asyncio. httpx would do, but this module is
        imported unconditionally (see pytest_plugins) and httpx is not always
        installed, so speak just enough HTTP/1.1 here.
        """
        async with await anyio.connect_tcp(host, self._port) as stream:
            await stream.send(
                f'GET /ok HTTP/1.1\r\nHost: {host}:{self._port}\r\n'
                f'Connection: close\r\n\r\n'.encode()
            )
            return await stream.receive()

    async def _wait_until_up(self):
        # A successful connect only means the socket is listening, which
        # happens before run_app can serve; wait for an actual response.
        for i in range(0, 30):
            if self.exitcode is not None:
                pytest.fail('unable to start/connect to aiohttp server')
                return

            try:
                with anyio.fail_after(0.5):
                    reply = await self._get_ok()
                if reply.startswith(b'HTTP/1.1 200'):
                    return
                await anyio.sleep(0.5)
            except (OSError, TimeoutError, anyio.EndOfStream):
                await anyio.sleep(0.5)
            except BaseException:
                pytest.fail('unable to start/connect to aiohttp server')
                raise

        pytest.fail('unable to start and connect to aiohttp server')


@pytest.fixture
async def moto_server(server_scheme):
    server = ThreadedMotoServer(port=0)
    try:
        server.start()
        host, port = server.get_host_and_port()
        yield f'http://{host}:{port}'
    finally:
        server.stop()
