import asyncio
import threading

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


# A thread with its own loop, not a subprocess: the spawned child took >30s to bind on 3.12 and only 3.12.
class AIOServer:
    """
    This is a mock AWS service which will 5 seconds before returning
    a response to test socket timeouts.
    """

    def __init__(self):
        self.endpoint_url = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._error = None
        self._thread = None

    def _run(self):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._error = exc
        finally:
            # Always unblock __aenter__, even if the loop died before binding.
            self._ready.set()
            loop.close()

    async def _serve(self):
        app = aiohttp.web.Application()
        app.router.add_route('*', '/ok', self.ok)
        app.router.add_route('*', '/{anything:.*}', self.stream_handler)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        # Port 0, published once bound: nothing can take it in between.
        site = aiohttp.web.TCPSite(runner, host, 0)
        await site.start()
        self.endpoint_url = site.name
        self._ready.set()
        await asyncio.to_thread(self._stop.wait)
        await runner.cleanup()

    async def __aenter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # __aexit__ only runs if __aenter__ returns, so a failed start stops here.
        if not await asyncio.to_thread(self._ready.wait, 30):
            self._shutdown()
            pytest.fail('mock server never bound a port')
        if self._error is not None:
            self._shutdown()
            raise self._error
        if self.endpoint_url is None:
            self._shutdown()
            pytest.fail('mock server thread exited before binding')
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._shutdown()

    def _shutdown(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

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
        await resp.write(b'')
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
