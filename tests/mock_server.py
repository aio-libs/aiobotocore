import asyncio
import aiohttp
import aiohttp.web
from aiohttp.web import StreamResponse
import pytest
import requests
import signal
import subprocess as sp
import sys
import time
import socket
import multiprocessing


_proxy_bypass = {
  "http": None,
  "https": None,
}

host = "localhost"


def get_free_tcp_port():
    sckt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sckt.bind((host, 0))
    addr, port = sckt.getsockname()
    sckt.close()
    return port


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
        self._port = get_free_tcp_port()
        self.endpoint_url = 'http://{}:{}'.format(host, self._port)
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

    async def ok(self, request):
        return aiohttp.web.Response()

    async def stream_handler(self, request):
        # Without the Content-Type, most (all?) browsers will not render
        # partially downloaded content. Note, the response type is
        # StreamResponse not Response.
        resp = StreamResponse(status=200, reason='OK',
                              headers={'Content-Type': 'text/html'})

        await resp.prepare(request)
        await asyncio.sleep(5, loop=self._loop)
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


def start_service(service_name, host, port):
    args = [sys.executable, "-m", "moto.server", "-H", host,
            "-p", str(port), service_name]

    # If test fails stdout/stderr will be shown
    process = sp.Popen(args, stdin=sp.PIPE)
    url = "http://{host}:{port}".format(host=host, port=port)

    for i in range(0, 30):
        if process.poll() is not None:
            process.communicate()
            pytest.fail("service failed starting up: {}".format(service_name))
            break

        try:
            # we need to bypass the proxies due to monkeypatches
            requests.get(url, timeout=0.5, proxies=_proxy_bypass)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.5)
    else:
        stop_process(process)  # pytest.fail doesn't call stop_process
        pytest.fail("Can not start service: {}".format(service_name))

    return process


def stop_process(process):
    try:
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=20)
    except sp.TimeoutExpired:
        process.kill()
        outs, errors = process.communicate(timeout=20)
        exit_code = process.returncode
        msg = "Child process finished {} not in clean way: {} {}" \
            .format(exit_code, outs, errors)
        raise RuntimeError(msg)


@pytest.yield_fixture(scope="session")
def s3_server():
    host = "localhost"
    port = 5000
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('s3', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def dynamodb2_server():
    host = "localhost"
    port = 5001
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('dynamodb2', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def cloudformation_server():
    host = "localhost"
    port = 5002
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('cloudformation', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def sns_server():
    host = "localhost"
    port = 5003
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('sns', host, port)

    try:
        yield url
    finally:
        stop_process(process)


@pytest.yield_fixture(scope="session")
def sqs_server():
    host = "localhost"
    port = 5004
    url = "http://{host}:{port}".format(host=host, port=port)
    process = start_service('sqs', host, port)

    try:
        yield url
    finally:
        stop_process(process)
