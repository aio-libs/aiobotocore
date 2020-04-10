import asyncio
import multiprocessing

# Third Party
import aiohttp
import aiohttp.web
from aiohttp.web import StreamResponse
import pytest

# aiobotocore
from tests.moto_server import host, MotoService, get_free_tcp_port


_proxy_bypass = {
  "http": None,
  "https": None,
}


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
            aiohttp.web.run_app(app, host=host, port=self._port,
                                handle_signals=False)
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
        resp = StreamResponse(status=200, reason='OK',
                              headers={'Content-Type': 'text/html'})

        await resp.prepare(request)
        await asyncio.sleep(5)
        await resp.drain()
        return resp

    async def _wait_until_up(self):
        async with aiohttp.ClientSession() as session:
            for i in range(0, 30):
                if self.exitcode is not None:
                    pytest.fail('unable to start/connect to aiohttp server')
                    return

                try:
                    # we need to bypass the proxies due to monkey patches
                    await session.get(self.endpoint_url + '/ok', timeout=0.5)
                    return
                except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                    await asyncio.sleep(0.5)
                except BaseException:
                    pytest.fail('unable to start/connect to aiohttp server')
                    raise

        pytest.fail('unable to start and connect to aiohttp server')


@pytest.fixture
async def s3_server():
    async with MotoService('s3') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def dynamodb2_server():
    async with MotoService('dynamodb2') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def cloudformation_server():
    async with MotoService('cloudformation') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def sns_server():
    async with MotoService('sns') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def sqs_server():
    async with MotoService('sqs') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def batch_server():
    async with MotoService('batch') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def lambda_server():
    async with MotoService('lambda') as svc:
        yield svc.endpoint_url


@pytest.fixture
async def iam_server():
    async with MotoService('iam') as svc:
        yield svc.endpoint_url
