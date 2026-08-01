import asyncio
import multiprocessing

# Third Party
import aiohttp
import aiohttp.web
import pytest
from aiohttp.web import StreamResponse
from moto.server import ThreadedMotoServer

_proxy_bypass = {
    "http": None,
    "https": None,
}

host = '127.0.0.1'


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
        self._conn, child_conn = multiprocessing.Pipe()
        self._stop = multiprocessing.Event()
        super().__init__(target=self._run, args=(child_conn, self._stop))
        self.endpoint_url = None
        self.daemon = True  # die when parent dies

    def _run(self, conn, stop):
        asyncio.run(self._serve(conn, stop))

    async def _serve(self, conn, stop):
        app = aiohttp.web.Application()
        app.router.add_route('*', '/ok', self.ok)
        app.router.add_route('*', '/{anything:.*}', self.stream_handler)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        # Port 0 and report back: no window for another process to take it.
        site = aiohttp.web.TCPSite(runner, host, 0)
        await site.start()
        conn.send(site.name)
        await asyncio.to_thread(stop.wait)
        await runner.cleanup()

    async def __aenter__(self):
        self.start()
        # __aexit__ only runs if __aenter__ returns, so a failed start reaps here.
        try:
            self.endpoint_url = await self._await_bound_url()
        except BaseException:
            self._shutdown()
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._shutdown()

    async def _await_bound_url(self, timeout: float = 30) -> str:
        if not await asyncio.to_thread(self._conn.poll, timeout):
            pytest.fail(
                f'mock server never bound a port (exitcode={self.exitcode})'
            )
        return self._conn.recv()

    def _shutdown(self):
        self._stop.set()
        self.join(timeout=10)
        # terminate() does not wait, so join or the child keeps holding its port.
        if self.is_alive():
            self.terminate()
            self.join(timeout=5)
        if self.is_alive():
            self.kill()
            self.join(timeout=5)

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
        # Outlast the client read timeout, but return at once on shutdown.
        await asyncio.to_thread(self._stop.wait, 5)
        await resp.drain()
        return resp


@pytest.fixture
async def moto_server(server_scheme):
    server = ThreadedMotoServer(port=0)
    try:
        server.start()
        host, port = server.get_host_and_port()
        yield f'http://{host}:{port}'
    finally:
        server.stop()
